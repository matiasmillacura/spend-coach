# Spend Coach

[![CI](https://github.com/matiasmillacura/spend-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/matiasmillacura/spend-coach/actions/workflows/ci.yml)

A personal finance app where you log money by talking to it. You write
"gasté 12 lucas en almuerzo" (Chilean for "I spent 12k on lunch"), send a photo of
the receipt, or dictate it, and the expense lands in the database with its amount,
category and date. The other half of the app is a dashboard that turns those
entries into a budget you can actually read.

**Live:** <https://spend-coach-dzaa.onrender.com> — free Render instance, so the
first request after ~15 minutes of inactivity takes 30–60s to wake up.

Solo project: product spec, agent design, backend, frontend, deployment and CI.

---

## What it does

**The chat is an agent, not a form.** It has 16 tools and decides which to call.
It runs a short onboarding (name → date of birth → income → first savings goal),
then logs expenses, income and savings from plain language. It also fixes its own
mistakes: if you say "el almuerzo de ayer fueron 8 lucas, no 12", it lists your
recent entries, finds the right one and edits it.

It's decisive by design. "Ahorré el 10% del sueldo" doesn't trigger a question —
it computes 10% of your registered income, saves it, and confirms in one sentence.
Two alternative goals ("working holiday or a car, whichever comes first") become
one goal with a combined name, not an interrogation.

**The dashboard answers "am I okay this month?"** Income vs expenses, savings rate,
what's left to spend, deficit alerts, savings goals with a projected completion
date, a configurable 50/30/20 rule, spending by category, daily trend, and
month-over-month insights ("▲ delivery: +40% vs last month"). Once a week it drops
a written summary into the chat without being asked.

**It speaks Chilean Spanish.** The system prompt understands local money slang
(`luca` = 1,000, `gamba` = 100, `palo` = million) and explicitly bans River Plate
and peninsular Spanish, which is where an LLM drifts if you don't pin it down.

---

## How it works

### Two agent engines, one interface

The same agent is implemented twice, behind the same function signature:

- [`coach_agent.py`](coach_agent.py) — a hand-written tool-use loop on the raw
  Anthropic SDK. Full control over the message history and the tool round limit.
- [`coach_agent_lg.py`](coach_agent_lg.py) — LangGraph's `create_react_agent`,
  with conversation state persisted by a checkpointer (SQLite locally, Postgres in
  production) and a `pre_model_hook` that trims what the model sees without
  touching what's stored.

`COACH_ENGINE` picks one at boot. Writing the raw loop first and the framework
version second is how I learned what LangGraph is actually doing underneath, and
the pair is useful: when the framework version behaves oddly, there's a reference
implementation to compare against.

### Semantic search over your own spending

Category filters can't answer "how much have I spent on things related to my dog?"
So expenses get embedded (Voyage AI) and stored in a vector store — pgvector in
production, in-memory with SQLite — and the agent gets a
`buscar_gastos_similares` tool. See [`rag_gastos.py`](rag_gastos.py).

Two details that mattered:

- **Tenant isolation lives in the store filter, not the prompt.** The retrieval
  query is filtered by `user_id` before results come back, with a second check on
  the way out. A prompt instruction like "only show this user's expenses" is not a
  security boundary.
- **The tool is only offered when embeddings are configured.** No API key means
  `tools_disponibles()` doesn't include it, so the model can't call a tool that
  would fail.

### Model routing

Different jobs get different models: Sonnet for the conversation (it has to hold
context and pick tools), Opus for the monthly written analysis (called rarely,
quality matters), Haiku for cheap one-shot extraction. Cost per user stays low
because the expensive model isn't in the hot path.

LangSmith tracing is wired in, which gives a per-step tree of agent → model →
tools with tokens, cost and latency for each conversation.

---

## Stack

| Layer | What's used |
|---|---|
| LLM | Anthropic Claude API (direct SDK + LangChain), tool calling, vision for receipts |
| Agent | LangGraph `create_react_agent`, SQLite/Postgres checkpointers, message trimming |
| RAG | Voyage AI embeddings, pgvector via `langchain-postgres` |
| Backend | Python 3.12, Flask 3, SQLAlchemy 2.0 ORM |
| Data | PostgreSQL in production, SQLite locally, 11 tables all scoped by `user_id` |
| Auth | Google OAuth 2.0 (Authlib) and email/password, hashed sessions, Chilean RUT validation (mod 11) |
| Frontend | Vanilla JS, no framework. Mobile-first PWA with a service worker, hand-written SVG charts |
| Infra | Render (`render.yaml` as code), gunicorn, GitHub Actions CI, LangSmith tracing |
| Tests | pytest — auth, cross-tenant ownership, RAG filtering, amount parsing, RUT |

---

## Things I'd point at in a code review

- **Config refuses to boot insecurely.** In production, no session key or no
  configured login raises `ConfigError` instead of starting with a generated
  ephemeral secret. Demo mode can't turn itself on. [`config.py`](config.py)
- **Every data function takes `user_id` first**, and the ownership tests exist to
  prove one user can't read or delete another's rows. Multi-tenancy enforced in
  the data layer, not in the route handlers. [`db.py`](db.py)
- **The service worker never caches `/api/`.** Caching a stale balance would be
  worse than showing nothing. [`web/sw.js`](web/sw.js)
- **Errors don't lie to the user.** API failures map to specific messages ("the
  API is rate limited, try again in a few seconds"), the traceback goes to the
  logs, and the generic 500 path says something happened rather than pretending
  the message went through. [`app.py`](app.py)
- **Gunicorn is tuned to the instance, not to a default.** One worker with four
  threads because the free tier has 512 MB and each worker loads the whole stack;
  a 120s timeout because an agent turn with tool calls exceeds the default 30s.
  [`render.yaml`](render.yaml)
- **Receipt photos are compressed client-side** before they're base64'd into the
  request, with a size ceiling on the server.

---

## Code map

```
config.py         → configuration and production safety checks
db.py             → SQLAlchemy models + queries, everything per user_id
coach_agent.py    → the agent: 16 tool definitions, system prompt, tool-use loop
coach_agent_lg.py → same agent on LangGraph, with checkpointed memory
rag_gastos.py     → embeddings, vector store, per-user semantic search
dashboard.py      → financial analysis: balance, savings rate, goals, 50/30/20, insights
coach.py          → the monthly written commentary
extractor.py      → shared Anthropic client
auth.py           → Google OAuth, email/password, RUT validation, login_required
app.py            → Flask app: JSON API + static files
web/              → PWA frontend (chat + dashboard, SVG charts)
tests/            → pytest
```

Written in Spanish, because the product is: the code, the comments and the docs
match the language its users speak.

[`SPEC.md`](SPEC.md) holds the product and UX decisions the code implements.
[`DEPLOY.md`](DEPLOY.md) covers running it on Render and Neon.

## License

MIT — see [`LICENSE`](LICENSE).
