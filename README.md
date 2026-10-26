# Public Invoice Agent

An A2A-compatible FastAPI service for evidence-based invoice decisions, explicitly approved payments, and durable task/receipt handling.

## Deploy to Render

1. Push the repository to GitHub and create a Render Blueprint from `render.yaml`. It configures Uvicorn and a persistent disk mounted at `/var/data`.
2. The A2A endpoints are public and do not use an A2A bearer token. This is separate from `AI_API_KEY`, which is used for Groq invoice analysis.
3. Set `AI_API_KEY` to your GroqCloud API key. The default provider settings are `AI_BASE_URL=https://api.groq.com/openai/v1` and `AI_MODEL=openai/gpt-oss-20b`.
4. To execute payments, configure `PAYMENT_API_URL` and `PAYMENT_API_KEY` for your payment adapter. Without a payment adapter, the agent does not claim to have paid an invoice.
5. Use the Render service's HTTPS origin as the submitted A2A base URL, for example `https://your-service.onrender.com`—do not append `/message:send`.

The Agent Card is at `/.well-known/agent-card.json`. Render starts the app with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; do not use `--reload` in production.

## Routes and protocol

- `GET /health` — health check.
- `GET /.well-known/agent-card.json` — Agent Card; public.
- `GET /tasks` and `GET /tasks/{task_id}` — public task access.
- `POST /message:send` — public A2A send; requires `Content-Type: application/a2a+json`. `A2A-Version` may be omitted or set to `0.3.0` or `v3`.
- `POST /tasks/{task_id}:cancel` — public cancellation.
- `POST /` — public JSON-RPC A2A methods.
- `POST /process` — direct JSON API, not an A2A endpoint.

A2A responses use `Content-Type: application/a2a+json`. An unsupported explicit A2A version returns HTTP 400; a wrong request media type returns HTTP 415. The `v3` header value is accepted as the validator's alias for this protocol version.

## Local development and complete curl checks

Install dependencies and start the app in terminal 1:

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

In terminal 2, from the project directory:

```bash
export BASE_URL=http://localhost:8000

# Health and public discovery.
curl -i "$BASE_URL/health"
curl -i "$BASE_URL/"
curl -i "$BASE_URL/.well-known/agent-card.json"

# Protocol rejection checks: wrong media type -> HTTP 415; wrong version -> HTTP 400.
curl -sS -o /dev/null -w 'wrong media type: HTTP %{http_code}\n' \
  -X POST "$BASE_URL/message:send" \
  -H 'Content-Type: application/json' \
  -H 'A2A-Version: 0.3.0' \
  -d '{}'
curl -sS -o /dev/null -w 'wrong version: HTTP %{http_code}\n' \
  -X POST "$BASE_URL/message:send" \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Version: 99.0' \
  -d '{}'

# A2A send: expect HTTP 200 and Content-Type: application/a2a+json.
curl -i -X POST "$BASE_URL/message:send" \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Version: 0.3.0' \
  -H 'Accept: application/a2a+json' \
  -d '{
    "taskId": "local-a2a-001",
    "package": {
      "invoice": {"invoice_id": "INV-105", "amount": 80, "currency": "USD"},
      "text": "Invoice INV-105 total USD 80.00"
    }
  }'

# Validator compatibility: the assignment sends A2A-Version: v3 and may include
# a Bearer token. The endpoint accepts both; expect HTTP 200.
curl -i -X POST "$BASE_URL/message:send" \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Version: v3' \
  -H 'Authorization: Bearer local-test-token' \
  -H 'Accept: application/a2a+json' \
  -d '{"taskId":"local-a2a-v3","package":{}}'

# List tasks (expect HTTP 200).
curl -i "$BASE_URL/tasks" 

# Get the task just submitted (expect HTTP 200).
curl -i "$BASE_URL/tasks/local-a2a-001" 

# Direct invoice-processing endpoint, independent of A2A auth.
curl -i -X POST "$BASE_URL/process" \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id": "local-direct-001",
    "package": {
      "invoice": {"invoice_id": "INV-104", "amount": 125, "currency": "USD"},
      "text": "Invoice INV-104 total USD 125.00"
    }
  }'

# A2A cancellation (expect HTTP 200 or a conflict if task already completed).
curl -i -X POST "$BASE_URL/tasks/local-a2a-001:cancel" \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Version: 0.3.0'
```

## Test Render

Set `BASE_URL` to the Render HTTPS origin (no path suffix), then run the A2A curl checks above. No A2A token is needed.

## Invoice safety behavior

An approval must explicitly match the invoice ID and currency, and its amount cap must cover the payment amount. The AI cannot grant approval. A2A task state and receipt records are stored in SQLite on Render's persistent disk. Payment idempotency is invoice-scoped; the payment adapter should also honor the supplied idempotency key to protect against network failures during payment processing.
