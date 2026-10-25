# Public Invoice Agent

An A2A-compatible FastAPI service for reviewing invoice packages, returning evidence-backed decisions, and executing a payment only when an explicit, invoice-specific approval is present. SQLite records task results and payment receipts so retries do not repeat a payment.

## Deploy to Render

1. Push this repository to GitHub and create a Render **Blueprint** from it. `render.yaml` provisions the web service and a 1 GB persistent disk mounted at `/var/data`.
2. Set `AI_API_KEY` to a key for an OpenAI-compatible chat-completions provider. `AI_BASE_URL` and `AI_MODEL` can be changed for another compatible provider.
3. To execute payments, set `PAYMENT_API_URL` and `PAYMENT_API_KEY` for your payment adapter. It must accept a JSON `POST` with the invoice ID, amount, currency, reason, and citations; return a non-empty JSON receipt on success; and honor the `Idempotency-Key` header. Without a configured adapter, the agent never claims a payment was made.
4. Set `PUBLIC_BASE_URL` to the deployed HTTPS URL if the A2A Agent Card should advertise an absolute URL.

The app listens on Render's `$PORT`. Its A2A base URL is the service root. The deployed Agent Card is at `/.well-known/agent-card.json`.

## Interfaces

- `GET /health` — health check.
- `GET /.well-known/agent-card.json` — A2A Agent Card.
- `POST /message:send` — A2A message-send endpoint (accepts direct task parameters or JSON-RPC); A2A responses use `Content-Type: application/a2a+json`.
- `POST /` — JSON-RPC A2A `message/send` (also accepts `tasks/send`) and `tasks/get`; responses use `Content-Type: application/a2a+json`.
- `POST /process` — direct JSON API: `{"task_id":"optional-stable-id","package":{...}}`.

An invoice package may contain structured `invoice` data, a trusted `approval` object, document text, and/or A2A file parts encoded as base64. Approval must have this shape:

```json
{
  "approved": true,
  "invoice_id": "INV-104",
  "max_amount": 250.00,
  "currency": "USD"
}
```

The approval must match the exact invoice ID and currency, and its amount cap must cover the proposed payment. The AI cannot grant approval. When configured, the analysis provider is called for every new task; when it is not configured, the app uses conservative local extraction and will not auto-pay.

Payment idempotency is keyed by invoice ID, not task ID or amount, so a retry cannot pay that invoice again and a different invoice remains eligible for its own approved payment. An external payment adapter should independently honor the supplied idempotency key to cover network/process failure between its execution and receipt persistence. Configure Render's persistent disk: the default local SQLite path is only for development.

## Local development

Install dependencies and start the server in one terminal:

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

In a second terminal, run these checks from the project directory. They do not need an AI or payment provider configured; the sample invoice should be held rather than paid.

```bash
# 1. Service health and browser landing page.
curl -i http://localhost:8000/health
curl -i http://localhost:8000/

# 2. Agent Card.
curl -i http://localhost:8000/.well-known/agent-card.json

# 3. Direct invoice-processing API.
curl -i -X POST http://localhost:8000/process \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id": "local-direct-001",
    "package": {
      "invoice": {"invoice_id": "INV-104", "amount": 125, "currency": "USD"},
      "text": "Invoice INV-104 total USD 125.00"
    }
  }'

# 4. A2A checker endpoint. Confirm response Content-Type is application/a2a+json.
curl -i -X POST http://localhost:8000/message:send \
  -H 'Content-Type: application/a2a+json' \
  -H 'Accept: application/a2a+json' \
  -d '{
    "taskId": "local-a2a-001",
    "package": {
      "invoice": {"invoice_id": "INV-105", "amount": 80, "currency": "USD"},
      "text": "Invoice INV-105 total USD 80.00"
    }
  }'

# 5. JSON-RPC A2A alternative.
curl -i -X POST http://localhost:8000/ \
  -H 'Content-Type: application/a2a+json' \
  -H 'Accept: application/a2a+json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "request-1",
    "method": "message/send",
    "params": {
      "taskId": "local-rpc-001",
      "package": {
        "invoice": {"invoice_id": "INV-106", "amount": 45, "currency": "USD"},
        "text": "Invoice INV-106 total USD 45.00"
      }
    }
  }'

# 6. Retrieve a completed task using JSON-RPC.
curl -i -X POST http://localhost:8000/ \
  -H 'Content-Type: application/a2a+json' \
  -H 'Accept: application/a2a+json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "request-2",
    "method": "tasks/get",
    "params": {"id": "local-rpc-001"}
  }'
```

These commands assume the app is running locally on Uvicorn's default port, `8000`. For the A2A checks, `curl -i` displays the response headers; both A2A POST examples should return HTTP 200 and `Content-Type: application/a2a+json`. Do not use `--reload` in Render's start command.
