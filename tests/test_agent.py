import base64
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import os
import secrets

from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

os.environ["DATABASE_PATH"] = str(Path(__file__).parent / "test.sqlite3")
os.environ["A2A_API_TOKEN"] = "local-test-token"
os.environ.pop("AI_API_KEY", None)
os.environ.pop("PAYMENT_API_URL", None)
os.environ.pop("PAYMENT_API_KEY", None)

from app.main import app, initialize_database
import app.main as agent

client = TestClient(app)
A2A_HEADERS = {
    "Authorization": "Bearer local-test-token",
    "Content-Type": "application/a2a+json",
    "A2A-Version": "0.3.0",
}

def verify_a2a_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    expected_token = os.getenv("A2A_API_TOKEN")

    # Fail closed if the production secret is not configured.
    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="A2A_API_TOKEN is not configured",
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(credentials.credentials, expected_token):
        raise HTTPException(
            status_code=401,
            detail="Invalid Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True

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
    card = client.get("/.well-known/agent-card.json").json()
    assert card["skills"][0]["id"] == "invoice-decision"
    assert card["protocolVersion"] == "0.3.0"
    assert "application/a2a+json" in card["defaultInputModes"]
    assert card["security"][0]["bearerAuth"] == []


def test_a2a_routes_require_bearer_auth_and_protocol_headers() -> None:
    assert client.get("/tasks").status_code == 401
    assert client.post(
        "/message:send",
        json={"taskId": "missing-auth", "package": {}},
        headers={"Content-Type": "application/a2a+json", "A2A-Version": "0.3.0"},
    ).status_code == 401
    assert client.get(
        "/tasks", headers={"Authorization": "Bearer wrong-token"}
    ).status_code == 401
    assert client.post(
        "/message:send",
        json={"taskId": "wrong-auth", "package": {}},
        headers={**A2A_HEADERS, "Authorization": "Bearer wrong-token"},
    ).status_code == 401

    body = {"taskId": "protocol-check", "package": {}}
    wrong_version = client.post(
        "/message:send",
        json=body,
        headers={**A2A_HEADERS, "A2A-Version": "9.9.9"},
    )
    wrong_media = client.post(
        "/message:send",
        json=body,
        headers={**A2A_HEADERS, "Content-Type": "application/json"},
    )
    assert wrong_version.status_code == 400
    assert wrong_media.status_code == 415
    assert wrong_version.headers["content-type"] == "application/a2a+json"
    assert wrong_media.headers["content-type"] == "application/a2a+json"

    assert client.get("/tasks", headers={"Authorization": "Bearer local-test-token"}).status_code == 200
    assert client.get(
        "/tasks?limit=11", headers={"Authorization": "Bearer local-test-token"}
    ).status_code == 422
    assert client.get("/tasks/a2a-1").status_code == 401
    assert client.get(
        "/tasks/a2a-1", headers={"Authorization": "Bearer local-test-token"}
    ).status_code == 404


def test_invalid_continuation_is_rejected_and_tasks_can_be_canceled() -> None:
    invalid_continuation = client.post(
        "/message:send",
        json={
            "taskId": "invalid-continuation",
            "referenceTaskIds": ["missing-parent"],
            "package": {},
        },
        headers=A2A_HEADERS,
    )
    assert invalid_continuation.status_code == 409

    with agent.database() as connection:
        connection.execute(
            """
            INSERT INTO tasks(task_id, request_hash, status, created_at, updated_at)
            VALUES (?, ?, 'working', ?, ?)
            """,
            ("cancel-me", "hash", agent.utc_now(), agent.utc_now()),
        )
    canceled = client.post(
        "/tasks/cancel-me:cancel",
        headers=A2A_HEADERS,
    )
    assert canceled.status_code == 200
    assert canceled.json()["status"]["state"] == "canceled"
    continued = client.post(
        "/message:send",
        json={
            "taskId": "cancel-continuation",
            "referenceTaskIds": ["cancel-me"],
            "package": {},
        },
        headers=A2A_HEADERS,
    )
    assert continued.status_code == 409


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


def test_task_cannot_be_canceled_during_payment() -> None:
    now = agent.utc_now()
    with agent.database() as connection:
        connection.execute(
            "INSERT INTO tasks(task_id, request_hash, status, created_at, updated_at) VALUES (?, ?, 'working', ?, ?)",
            ("payment-in-flight", "hash", now, now),
        )
        connection.execute(
            """
            INSERT INTO invoice_actions(
                invoice_id, action, status, task_id, created_at, updated_at
            ) VALUES (?, 'pay', 'pending', ?, ?, ?)
            """,
            ("INV-RACE", "payment-in-flight", now, now),
        )
    response = client.post(
        "/tasks/payment-in-flight:cancel",
        headers=A2A_HEADERS,
    )
    assert response.status_code == 409
    assert client.get(
        "/tasks/payment-in-flight",
        headers={"Authorization": "Bearer local-test-token"},
    ).json()["status"]["state"] == "working"


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
        headers=A2A_HEADERS,
    )
    assert sent.status_code == 200
    assert sent.json()["result"]["id"] == "a2a-1"
    fetched = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 8, "method": "tasks/get", "params": {"id": "a2a-1"}},
        headers=A2A_HEADERS,
    )
    assert fetched.json()["result"]["status"]["state"] == "completed"
    task_list = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 10, "method": "tasks/list", "params": {}},
        headers=A2A_HEADERS,
    )
    assert task_list.status_code == 200
    assert any(task["id"] == "a2a-1" for task in task_list.json()["result"]["tasks"])


def test_a2a_message_send_endpoint_accepts_direct_and_jsonrpc_requests() -> None:
    package = {
        "invoice": {"invoice_id": "INV-HTTP", "amount": 25, "currency": "USD"},
        "text": "Invoice INV-HTTP total USD 25.00",
    }
    direct = client.post(
        "/message:send",
        json={"taskId": "message-endpoint-direct", "package": package},
        headers=A2A_HEADERS,
    )
    assert direct.status_code == 200
    assert direct.headers["content-type"] == "application/a2a+json"
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
        headers=A2A_HEADERS,
    )
    assert rpc.status_code == 200
    assert rpc.headers["content-type"] == "application/a2a+json"
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

    def package(invoice_id: str, amount: int = 42) -> dict:
        return {
            "invoice": {"invoice_id": invoice_id, "amount": amount, "currency": "USD"},
            "text": f"Invoice {invoice_id} total USD {amount}.00",
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
    conflicting_payment = client.post(
        "/process",
        json={"task_id": "paid-conflict", "package": package("INV-5", amount=43)},
    )

    assert rejected["action"] == "hold"
    assert first["payment"]["status"] == "executed"
    assert first["payment"]["receipt"]["invoice_id"] == "INV-5"
    assert first["payment"]["receipt"]["provider_receipt"] == {"receipt_id": "receipt-INV-5"}
    assert retry["payment"]["status"] == "already_processed"
    assert retry["payment"]["receipt"] == first["payment"]["receipt"]
    assert other_invoice["payment"]["status"] == "executed"
    assert conflicting_payment.status_code == 409
    assert len(requests) == 2
