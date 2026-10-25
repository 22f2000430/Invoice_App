import base64
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_PATH"] = str(Path(__file__).parent / "test.sqlite3")
os.environ.pop("AI_API_KEY", None)
os.environ.pop("PAYMENT_API_URL", None)
os.environ.pop("PAYMENT_API_KEY", None)

from app.main import app, initialize_database
import app.main as agent

client = TestClient(app)


def setup_function() -> None:
    database = Path(os.environ["DATABASE_PATH"])
    if database.exists():
        database.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    initialize_database()


def test_agent_card_and_health() -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert home.json()["agent_card"] == "/.well-known/agent-card.json"
    assert client.get("/favicon.ico").status_code == 204
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/.well-known/agent-card.json").json()["skills"][0]["id"] == "invoice-decision"


def test_payment_requires_explicit_matching_approval() -> None:
    response = client.post(
        "/process",
        json={
            "task_id": "task-a",
            "package": {
                "invoice": {"invoice_id": "INV-1", "amount": 120, "currency": "USD"},
                "text": "Invoice INV-1 total USD 120.00",
                "approval": {
                    "approved": True,
                    "invoice_id": "INV-OTHER",
                    "max_amount": 200,
                    "currency": "USD",
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["action"] == "hold"
    assert response.json()["payment"]["status"] == "not_applicable"


def test_task_retry_returns_saved_decision_and_rejects_changed_package() -> None:
    package = {
        "invoice": {"invoice_id": "INV-2", "amount": 30, "currency": "USD"},
        "text": "Invoice INV-2 total USD 30.00",
    }
    first = client.post("/process", json={"task_id": "stable", "package": package})
    retry = client.post("/process", json={"task_id": "stable", "package": package})
    changed = client.post(
        "/process",
        json={
            "task_id": "stable",
            "package": {"invoice": {"invoice_id": "INV-3"}, "text": "different"},
        },
    )
    assert first.json() == retry.json()
    assert changed.status_code == 409


def test_a2a_send_and_get_task() -> None:
    sent = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "message/send",
            "params": {
                "taskId": "a2a-1",
                "package": {
                    "invoice": {"invoice_id": "INV-3", "amount": 30, "currency": "USD"},
                    "text": "Invoice INV-3 total USD 30.00",
                },
            },
        },
    )
    assert sent.status_code == 200
    assert sent.json()["result"]["id"] == "a2a-1"
    fetched = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 8, "method": "tasks/get", "params": {"id": "a2a-1"}},
    )
    assert fetched.json()["result"]["status"]["state"] == "completed"


def test_a2a_message_send_endpoint_accepts_direct_and_jsonrpc_requests() -> None:
    package = {
        "invoice": {"invoice_id": "INV-HTTP", "amount": 25, "currency": "USD"},
        "text": "Invoice INV-HTTP total USD 25.00",
    }
    direct = client.post(
        "/message:send",
        json={"taskId": "message-endpoint-direct", "package": package},
    )
    assert direct.status_code == 200
    assert direct.json()["id"] == "message-endpoint-direct"
    assert direct.json()["status"]["state"] == "completed"

    rpc = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "message/send",
            "params": {"taskId": "message-endpoint-rpc", "package": package},
        },
    )
    assert rpc.status_code == 200
    assert rpc.json()["result"]["id"] == "message-endpoint-rpc"


def test_base64_text_attachment_is_read() -> None:
    content = base64.b64encode(b"Invoice INV-4 total USD 10.00").decode()
    response = client.post(
        "/process",
        json={
            "task_id": "attachment",
            "package": {
                "parts": [
                    {
                        "file": {
                            "name": "invoice.txt",
                            "mimeType": "text/plain",
                            "data": content,
                        }
                    }
                ]
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["invoice_id"] == "INV-4"
    assert response.json()["evidence"]


def test_payment_receipts_are_durable_and_invoice_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []

    class FakeClient:
        def __init__(self, timeout: int):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url: str, json: dict, headers: dict) -> httpx.Response:
            requests.append(json)
            return httpx.Response(
                200,
                json={"receipt_id": f"receipt-{json['invoice_id']}"},
                request=httpx.Request("POST", url),
            )

    async def approve(package: dict, text: str) -> dict:
        invoice = package["invoice"]
        return {
            "invoice_id": invoice["invoice_id"],
            "action": "pay",
            "reason": "Approved invoice with matching amount.",
            "citations": (
                [f"Invoice {invoice['invoice_id']}"]
                if package.get("unsupported_citation")
                else [text]
            ),
            "amount": invoice["amount"],
            "currency": invoice["currency"],
        }

    monkeypatch.setattr(agent, "AI_API_KEY", "test-key")
    monkeypatch.setattr(agent, "PAYMENT_API_URL", "https://payments.example/pay")
    monkeypatch.setattr(agent, "PAYMENT_API_KEY", "test-payment-key")
    monkeypatch.setattr(agent.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(agent, "analyze_invoice", approve)

    def package(invoice_id: str) -> dict:
        return {
            "invoice": {"invoice_id": invoice_id, "amount": 42, "currency": "USD"},
            "text": f"Invoice {invoice_id} total USD 42.00",
            "approval": {
                "approved": True,
                "invoice_id": invoice_id,
                "max_amount": 50,
                "currency": "USD",
            },
        }

    unsupported = package("INV-UNSUPPORTED")
    unsupported["unsupported_citation"] = True
    rejected = client.post(
        "/process", json={"task_id": "unsupported-citation", "package": unsupported}
    ).json()
    first = client.post("/process", json={"task_id": "paid-1", "package": package("INV-5")}).json()
    retry = client.post("/process", json={"task_id": "paid-2", "package": package("INV-5")}).json()
    other_invoice = client.post(
        "/process", json={"task_id": "paid-3", "package": package("INV-6")}
    ).json()

    assert rejected["action"] == "hold"
    assert first["payment"] == {"status": "executed", "receipt": {"receipt_id": "receipt-INV-5"}}
    assert retry["payment"]["status"] == "already_processed"
    assert retry["payment"]["receipt"] == first["payment"]["receipt"]
    assert other_invoice["payment"]["status"] == "executed"
    assert len(requests) == 2
