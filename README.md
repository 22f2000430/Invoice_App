# Public Invoice Agent

An A2A-compatible FastAPI service for evidence-based invoice decisions, explicitly approved payments, and durable task/receipt handling.

## Deploy to Render

1. Push the repository to GitHub and create a Render Blueprint from `render.yaml`. It configures Uvicorn and a persistent disk mounted at `/var/data`.
2. In the Render service's **Environment** settings, set `A2A_API_TOKEN` to a long, random secret. The A2A client/checker must send this exact value in an `Authorization: Bearer <token>` header. This is separate from `AI_API_KEY`.
3. Set `AI_API_KEY` to your GroqCloud API key. The default provider settings are `AI_BASE_URL=https://api.groq.com/openai/v1` and `AI_MODEL=openai/gpt-oss-20b`.
4. To execute payments, configure `PAYMENT_API_URL` and `PAYMENT_API_KEY` for your payment adapter. Without a payment adapter, the agent does not claim to have paid an invoice.
5. Use the Render service's HTTPS origin as the submitted A2A base URL, for example `https://your-service.onrender.com`—do not append `/message:send`.

The Agent Card is at `/.well-known/agent-card.json`. Render starts the app with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; do not use `--reload` in production.

## Routes and protocol

- `GET /health` — health check.
- `GET /.well-known/agent-card.json` — Agent Card; public.
- `GET /tasks` and `GET /tasks/{task_id}` — require Bearer auth.
- `POST /message:send` — A2A send; requires Bearer auth, `Content-Type: application/a2a+json`, and `A2A-Version: 0.3.0`.
- `POST /tasks/{task_id}:cancel` — authenticated cancellation.
- `POST /` — authenticated JSON-RPC A2A methods.
- `POST /process` — direct JSON API, not an A2A endpoint.

A2A responses use `Content-Type: application/a2a+json`. Missing or wrong Bearer credentials return HTTP 401. An unsupported A2A version returns HTTP 400; a wrong request media type returns HTTP 415.

## Local development and complete curl checks

Install dependencies and start the app in terminal 1:

```bash
python -m pip install -r requirements.txt
export A2A_API_TOKEN=local-test-token
uvicorn app.main:app --reload
```

In terminal 2, from the project directory:

```bash
export BASE_URL=http://localhost:8000
export A2A_API_TOKEN=local-test-token

# Health and public discovery.
curl -i "$BASE_URL/health"
curl -i "$BASE_URL/"
curl -i "$BASE_URL/.well-known/agent-card.json"

# Authentication checks: both should return HTTP 401.
curl -sS -o /dev/null -w 'no token: HTTP %{http_code}\n' "$BASE_URL/tasks"
curl -sS -o /dev/null -w 'wrong token: HTTP %{http_code}\n' \
  -H 'Authorization: Bearer wrong-token' "$BASE_URL/tasks"

# Protocol rejection checks: wrong media type -> HTTP 415; wrong version -> HTTP 400.
curl -sS -o /dev/null -w 'wrong media type: HTTP %{http_code}\n' \
  -X POST "$BASE_URL/message:send" \
  -H "Authorization: Bearer $A2A_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'A2A-Version: 0.3.0' \
  -d '{}'
curl -sS -o /dev/null -w 'wrong version: HTTP %{http_code}\n' \
  -X POST "$BASE_URL/message:send" \
  -H "Authorization: Bearer $A2A_API_TOKEN" \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Version: 99.0' \
  -d '{}'

# A2A send: expect HTTP 200 and Content-Type: application/a2a+json.
curl -i -X POST "$BASE_URL/message:send" \
  -H "Authorization: Bearer $A2A_API_TOKEN" \
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

# List tasks (expect HTTP 200).
curl -i "$BASE_URL/tasks" \
  -H "Authorization: Bearer $A2A_API_TOKEN"

# Get the task just submitted (expect HTTP 200).
curl -i "$BASE_URL/tasks/local-a2a-001" \
  -H "Authorization: Bearer $A2A_API_TOKEN"

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
  -H "Authorization: Bearer $A2A_API_TOKEN" \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Version: 0.3.0'
```

## Test Render

Set `BASE_URL` to the Render HTTPS origin (no path suffix). Set `A2A_API_TOKEN` in the terminal to the exact secret saved in Render, then run the authenticated A2A curl checks above.

If curl with the Render token returns 200 but the checker returns 401, the checker did not send that same Bearer token. Configure its authentication/token field with the Render `A2A_API_TOKEN` if it provides one. The Agent Card advertises HTTP Bearer authentication, but it does not reveal the secret. Do not disable auth: unauthenticated task listing and sending are expected to return 401.

## Invoice safety behavior

An approval must explicitly match the invoice ID and currency, and its amount cap must cover the payment amount. The AI cannot grant approval. A2A task state and receipt records are stored in SQLite on Render's persistent disk. Payment idempotency is invoice-scoped; the payment adapter should also honor the supplied idempotency key to protect against network failures during payment processing.
