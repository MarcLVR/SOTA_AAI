# AI Auditor — Document Compliance for AI-Readiness

Audit your document repositories before feeding them into a RAG pipeline. Scores every file on staleness, standards compliance, and governance metadata — no LLM calls, no quotas, no waiting.

Built on **LangGraph + Claude (Anthropic)**.

---

## What it does

| Signal | What it catches |
|---|---|
| **Staleness** | Files past their review cadence, cold-access documents, overdue dates in body text |
| **Standards** | Missing required sections, non-standard formats, retired standard references (ISO 27001:2013, "proposed AI Act", …) |
| **Governance** | No named owner, missing classification, absent retention or review date |

Each document gets a **trust score 0 → 1**. Anything below **0.70** is flagged before it can mislead your RAG system.

The multi-agent chat (Tab 3) then lets you ask questions, get remediation advice, and run web research — all with Reflexion self-critique and optional human-in-the-loop approval.

---

## Architecture

```
guardrail_in → supervisor → researcher / coder / auditor / general
                                        ↓
                                      critic  (Reflexion, off by default)
                                        ↓
                                       hitl → END
```

- **Domain pipeline** — pure Python, zero LLM calls. Runs 42 docs in ~1 s.
- **Supervisor** — structured routing via `SupervisorDecision` (Pydantic + Claude Haiku).
- **Critic** — Groq Llama-3.3-70B scores 0–1; routes back if score < 0.70 (opt-in via `CRITIC_ENABLED=true`).
- **HITL** — LangGraph `interrupt()` pauses execution to disk; resumes on approval.
- **Memory** — ChromaDB (RAG) + Mem0 episodic; conversation checkpointed to SQLite.

---

## Quick start

**Requirements:** Python 3.11, an Anthropic API key.

```bash
git clone https://github.com/OAPMaaS/dave.git && cd dave
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add ANTHROPIC_API_KEY (and GROQ_API_KEY for the critic)

python -m domain.demo_corpus.generate_sample   # generate 42-doc synthetic corpus
python main.py                                 # → http://localhost:7860
```

**Other modes:**

```bash
python main.py --cli           # interactive terminal chat
python main.py --query "..."   # single-shot query
```

---

## Configuration

All variables are read from `.env` (or the shell environment).

### LLM

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` · `groq` · `ollama` |
| `ANTHROPIC_API_KEY` | — | Required |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | |
| `GROQ_API_KEY` | — | Required for the critic role |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `llama3.2` | |

**Per-role overrides** — pin any role to a different provider or model:

```env
ROLE_PROVIDER_CRITIC=groq
ROLE_MODEL_EXECUTOR=claude-sonnet-4-6
```

Valid roles: `supervisor` `researcher` `coder` `general` `auditor` `critic` `executor`

### Agent behaviour

| Variable | Default | Notes |
|---|---|---|
| `MAX_SUPERVISOR_ROUNDS` | `5` | Hard cap on routing loops |
| `CRITIC_ENABLED` | `false` | Reflexion loop; off saves one LLM call per answer |
| `CRITIC_REVISION_THRESHOLD` | `0.70` | Score below this triggers a revision |
| `CRITIC_MAX_REVISIONS` | `2` | Max revision rounds per turn |

### Application identity

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | `AI Auditor` | Shown in UI and bot messages |
| `BRAND_NAME` | _(empty)_ | Canonical brand name to enforce; empty disables brand checking |
| `DEFAULT_OWNER` | _(empty)_ | Fallback owner when doc author cannot be resolved |
| `OWNER_USERNAMES` | _(empty)_ | Comma-separated owner usernames, e.g. `alice,bob` |

### Integrations (all optional)

| Variable | Notes |
|---|---|
| `LANGFUSE_PUBLIC_KEY` | Activates Langfuse cloud tracing |
| `SEMANTIC_ENABLED` | `false` — set `true` to enable the per-document standards comparison button |
| `DB_ENABLED` | `false` — set `true` to activate the Analytics tab and Postgres writes |
| `TELEGRAM_BOT_TOKEN` | Activates the Telegram notification pipeline |
| `TELEGRAM_OWNERS` | Comma-separated owner usernames for Telegram routing |

---

## CLI reference

```bash
# Audit any folder
python -m domain.run_audit /path/to/docs
python -m domain.run_audit /path/to/docs --json    # machine-readable output
python -m domain.run_audit /path/to/docs --top 20  # worst 20 documents

# Inspect a single document
python -m domain.inspect_doc path/to/file.docx
python -m domain.inspect_doc path/to/file.pdf --json | jq '.findings'

# Evals
python -m eval.agent_eval          # routing + e2e
python -m eval.rag_eval            # RAGAS metrics
```

---

## Project structure

```
├── main.py                   # entry point (UI / CLI / single query)
├── config/settings.py        # all env vars → pydantic Settings
├── agents/                   # LLM factory, supervisor, specialists, critic, executor
├── graph/                    # LangGraph state machine (state.py + workflow.py)
├── domain/                   # audit engine — zero LLM calls
│   ├── knowledge.py          # thresholds, retired standards, required sections
│   ├── run_audit.py          # programmatic API + CLI
│   ├── tools/                # crawler, extractor, staleness, standards, governance, aggregate
│   └── demo_corpus/          # 42-file synthetic corpus generator
├── standards/                # 6 markdown governance docs (RAG + semantic layer)
├── memory/                   # ChromaDB (RAG) + Mem0 (episodic)
├── guardrails/               # injection detection + PII redaction
├── chase/                    # Telegram bot + Postgres notifier (auto-starts when configured)
├── ui/app.py                 # Gradio 4-tab dashboard
└── eval/                     # agent routing + RAGAS evals
```

---

## Roadmap

- [x] Docker image
- [x] Telegram notifier daemon
- [x] Analytics tab with Postgres backend
- [x] Semantic compliance layer (`SEMANTIC_ENABLED`)
- [x] Metadata consistency check (author, never-revised, body-date skew)
- [x] Per-role token caps
- [x] Owner routing via document author metadata
- [ ] Docker Compose (one-command start with Postgres)
- [ ] PDF report generation
- [ ] Streaming output from specialist agents
- [ ] Browser agent via Playwright MCP

---

## License

MIT
