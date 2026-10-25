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
- `POST /message:send` — A2A message-send endpoint (accepts direct task parameters or JSON-RPC).
- `POST /` — JSON-RPC A2A `message/send` (also accepts `tasks/send`) and `tasks/get`.
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

```sh
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

To smoke-test without external services:

```sh
curl http://localhost:8000/health
curl -X POST http://localhost:8000/process \
  -H 'content-type: application/json' \
  -d '{"task_id":"demo-1","package":{"invoice":{"invoice_id":"INV-104","amount":125,"currency":"USD"},"text":"Invoice INV-104 total USD 125.00"}}'
```
