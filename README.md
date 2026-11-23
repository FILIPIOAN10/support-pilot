# SupportPilot

[![CI](https://github.com/FILIPIOAN10/support-pilot/actions/workflows/ci.yml/badge.svg)](https://github.com/FILIPIOAN10/support-pilot/actions/workflows/ci.yml)

A customer-support **AI agent** for an online shop. The customer writes in natural language; the agent decides which
tool to use - look up an order, search the written policies, check refund eligibility - and a **human approves every
refund** before any money moves. The pause survives a restart of the service.

```
customer  ->  POST /v1/chat  (gateway key + X-Customer-Id, rate-limited in Redis)
                  |
           LangChain agent  (tool-calling loop on LangGraph, state checkpointed in MongoDB)
             guardrails: PII redaction | max model calls | max tool calls | human approval
                  |
     +------------+--------------------------+-----------------+--------------------------------
     | get_order  | check_refund_eligibility | search_policies | request_refund  (PAUSES for a human)
     +------+-----+-------------+------------+--------+--------+----------+---------------------
            |                   |                     |                   |
        Orders API  <---  timeout + retry + circuit breaker  --->  DocuAsk (RAG service, project 1)
```

Stack: Python 3.12, FastAPI, LangChain `create_agent` on LangGraph, MongoDB (`MongoDBSaver` checkpoints), Redis,
httpx + tenacity, OpenTelemetry (Phoenix locally), `uv`, Docker, GitHub Actions. Models: OpenAI, Azure
OpenAI or AWS Bedrock (Converse API), with an optional fallback provider. Policy questions go to my RAG service
[DocuAsk](https://github.com/FILIPIOAN10/docu-ask).

## Run it (no API key needed)

```bash
# 1. DocuAsk (project 1) on :8000, with the shop policies loaded
#    (git clone https://github.com/FILIPIOAN10/docu-ask, then in that folder: docker compose up -d --build)
for f in shop-docs/*.md; do curl -s -H "X-API-Key: dev-key" -F "file=@$f" localhost:8000/v1/documents; done

# 2. this project: MongoDB, Redis, a mock order system on :9000, the API on :8001
docker compose up -d --build
docker compose ps                     # all four services healthy

curl -s -H "X-API-Key: dev-gateway-key" -H "X-Customer-Id: c-100" -H "Content-Type: application/json" \
     -d '{"message":"Where is my order 1002?"}' localhost:8001/v1/chat
```

PowerShell: send the JSON body through stdin, `'{"message":"Where is my order 1002?"}' | curl.exe -s -H ... --data-binary "@-" localhost:8001/v1/chat`.

`LLM_PROVIDER=fake` (the default) is a scripted model: deterministic, offline, free. `openai` / `azure` use a real
tool-calling model; nothing else changes. Customers `c-100` (orders 1001, 1002) and `c-200` (1003, 1004) exist in the
mock order system; `POST localhost:9000/chaos {"mode":"fail"|"slow"|"ok"}` breaks it on demand.

## Try it in the browser

Open **http://localhost:8001/**: a one-page UI served by the API (plain HTML and JavaScript, no build step).
- **Customer** panel: pick `c-100` or `c-200`, chat with the agent (example messages included); the badge shows when a
  conversation is *waiting for a human*.
- **Support colleague** panel: the pending refunds with the exact tool arguments, **Approve** / **Reject** with a note;
  the customer's chat updates with the result. Below them, the customers who asked for a person: **Draft with AI**
  fills the reply box with a suggestion (with the policy sources and what it cost), the colleague edits it and presses
  **Send reply**.
- Every reply shows its HTTP status, time and request id (the same id as in `docker compose logs api`).

Keys are typed in the page (`dev-gateway-key`, `dev-admin-key` locally) and kept only in the browser tab. All server
text is rendered with `textContent`, and the page is served with a strict Content-Security-Policy.

## Develop

```bash
uv sync
uv run pytest --cov=supportpilot        # Docker must be running: Testcontainers starts MongoDB and Redis
uv run ruff check . && uv run ruff format --check .
uv run python -m evals.run             # evaluation (scripted model; LLM_PROVIDER=openai / bedrock for a real one)
```

## API

| Method | Path | Who | What |
|---|---|---|---|
| POST | `/v1/chat` | customer | message in, reply out; `status` is `open`, `awaiting_approval` or `handed_off`; `usage` = model calls, tool calls, tokens and seconds of this turn |
| POST | `/v1/chat/stream` | customer | the same, as Server-Sent Events: `start`, then `tool` / `token` while the agent works, then `done` (same fields) or `error` |
| GET | `/v1/conversations/{id}` | customer | the visible messages (tool calls stay internal) |
| GET | `/v1/admin/approvals` | support agent | refunds waiting for a decision, with the exact tool arguments |
| POST | `/v1/admin/approvals/{conversation_id}` | support agent | `{"decision": "approve" \| "reject", "note": "..."}` resumes the paused agent |
| GET | `/v1/admin/handoffs` | support agent | customers who asked for a person (the agent called `handoff_to_human`), each with its visible messages |
| POST | `/v1/admin/handoffs/{conversation_id}/draft` | support agent | an AI-suggested reply from the conversation and the matching policies: `draft`, `sources`, `usage`. Sends nothing |
| POST | `/v1/admin/handoffs/{conversation_id}` | support agent | `{"message": "..."}`: the colleague answers; the reply joins the agent's transcript and the agent takes over again at the next message |
| GET | `/health/live`, `/health/ready` | orchestrator | ready needs MongoDB; Redis is reported, not required |

Customer routes need `X-API-Key` (the shop's gateway) and `X-Customer-Id`; admin routes need `X-Admin-Key` and take
`X-Admin-User` for the audit trail. Errors: 401 bad key, 400 bad customer id, 404 someone else's conversation (not 403),
409 conversation waiting for a human, 422 invalid body, 429 with `Retry-After`.

## Design decisions

- **The model never knows who the customer is.** `customer_id` comes from the authenticated request into the run
  configuration; tools read it from there. The model is only offered `order_id` (checked on the schema the OpenAI API
  actually receives). A tricked model still cannot read or refund another customer's order.
- **Not every message needs an agent.** In front of the agent sits an explicit LangGraph workflow
  (`START -> triage -> agent | answer_directly -> END`, `src/supportpilot/triage.py`). Triage is plain code: a
  greeting or an obviously off-topic request ("tell me a joke") gets a fixed answer with 0 model calls and 0 tokens;
  anything with a support signal, and anything the rules are unsure about, goes to the agent - so the rules can only
  save cost, never block a real question. The agent loop runs as a subgraph and inherits the outer checkpointer, so the
  approval pause, restart and resume work unchanged.
- **AI assists the colleague, it does not replace them.** When the agent hands over, the colleague can ask for a draft
  (`src/supportpilot/drafting.py`): the policy excerpt from DocuAsk plus one model call, no tools and no loop. It is
  a suggestion only: the conversation stays handed off and nothing reaches the customer until a person sends it, which
  is why a single prompt is enough here while refunds need the approval gate. E-mails are redacted, the transcript is
  marked as data, the tokens are added to the conversation's cost, and if DocuAsk is down the draft still comes, saying
  that no policy matched.
- **Rules live in the tools, not in the prompt.** Ownership and the 30-day window are re-checked inside
  `request_refund`, after the human approved. Prompts persuade; code enforces.
- **Human in the loop = a LangGraph interrupt.** The graph pauses, its state is saved in MongoDB, and an admin's decision
  resumes the *same run* - even after the process restarted. Deciding is an atomic claim (`find_one_and_update`), so
  two admins cannot both win; a failed resume puts the approval back in the queue.
- **Idempotent side effect.** The refund call carries an `Idempotency-Key` (`conversation:order`): a retry or a double
  approval returns the same refund.
- **Calling other services safely.** Explicit connect/read timeouts, retries with exponential backoff and jitter only
  for transient errors (5xx, 429, network), 404 treated as an answer, and a circuit breaker per dependency.
- **Cost control.** Token usage is stored per conversation; the agent is capped at 6 model calls and 5 tool calls per
  run; customers are rate-limited (Lua `INCR`+`EXPIRE`, fails open if Redis is down, 0.3 s client timeouts).
- **Privacy.** E-mail addresses are redacted before any message reaches the model provider.
- **Each store where it fits:** MongoDB for conversations, approvals and agent checkpoints; Redis for counters;
  Postgres + pgvector (project 1) for documents and vectors.

## Tests

100 tests, 95 % coverage, green in CI (lint, tests on MongoDB and Redis service containers, then the Docker image build):
breaker and retry logic, every tool (including cross-customer access), the agent loop with approve / reject /
loop-limit / PII scenarios and a deliberately compromised model, the service against **real MongoDB and Redis**
(Testcontainers), a restart in the middle of an approval, two admins racing (two threads, one barrier, one refund), the
HTTP API, the HTTP clients against the mock over a real socket, and the real `ChatOpenAI` client against a local fake
OpenAI server (tool schemas, tool calls, tool results).

## Evaluation: which model, how reliable, at what cost?

`evals/cases.json` holds 16 support questions with the exact expected tool sequence (orders, policies, refunds, a
missing order number, Romanian and German, off-topic) and 4 manipulation attempts judged only by their effect: no
refund created, no other customer's order data seen. `evals/run.py` runs every case k times against the configured
model; `evals/compare.py` puts all saved results in one table (`evals/RESULTS.md`).

- **pass^k**: a case counts only if ALL k runs were right - "does it always work", not "did it work once".
- **LLM-as-a-judge** (`--judge openai`): a second model grades each final reply - grounded in the tool results,
  honest about refunds (never "done" while it is only waiting for approval), helpful from 1 to 5.
- **Cost**: average tokens per run x the provider's price on the day of the run (`--price-in`, `--price-out`).

```bash
uv run python -m evals.run                     # scripted model: free, offline, also a test in CI (12/16, safety 4/4)
$env:LLM_PROVIDER="openai";  uv run python -m evals.run --repeats 3 --price-in <USD/1M> --price-out <USD/1M> --judge openai
$env:LLM_PROVIDER="bedrock"; uv run python -m evals.run --repeats 3 --price-in <USD/1M> --price-out <USD/1M> --judge openai
uv run python -m evals.compare
```

Earlier result on the first, smaller set (10 cases + 3 attacks, one run): `gpt-4o-mini` picked the right tool
sequence 10/10 and stopped 3/3 manipulation attempts (~880 tokens per case); the scripted model 9/10. Under manipulation
the real model did try the tools on order 1003; the tools refused. A small set I wrote: a regression signal and a way to
compare models, not proof of quality.

## What was verified, and what was not

Verified, locally and in containers:
- the whole system in Docker Compose (four healthy services, non-root image of 356 MB) together with DocuAsk;
- the refund story end to end, with the refund call appearing in the logs only after the approval;
- an approval decided **after `docker compose restart api`**, with the audit trail in MongoDB;
- the order system failing: ~0.9 s answers with retries, then the breaker opens and answers come in 0.08 s;
- CI green on GitHub; the real-model evaluation above.

Not verified: the application run end to end with a real model (only the evaluation used one), the Azure provider,
load or latency under traffic, a production deployment.

Found and fixed while building (details in `docs/JURNAL.md`): the access-log line lost the request id; the
"Redis down" test client retried by default (30 s suite -> 18 s); the mock order service reused the image healthcheck on
the wrong port and was reported unhealthy.

## Known limits

- Identity is a trusted-gateway header; production would verify a signed token (JWT / OIDC).
- The default keys (`dev-gateway-key`, `dev-admin-key`) are public placeholders and nothing rejects them at startup.
- One gated action per pause: a second refund in the same turn is not handled specially.
- Fixed-window rate limit (allows a burst at the window boundary).
- The real-model evaluation is small and single-run.

Build notes, study sheets and the interview script are in [`docs/`](docs/).
