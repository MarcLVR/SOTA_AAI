# SOTA Upgrade — Audit & Plan

> Status: **Phase 1 (audit) complete. Awaiting approval before Phase 2.**

## 0. Premise correction (read first)

The task brief describes this repo as "currently Ollama-only" and asks to "refactor
`agents/llm.py` into a provider factory." **That work is already done.** Current reality:

- `agents/llm.py` is already a multi-provider factory: `anthropic` (default), `groq`,
  `ollama`, with per-role provider/model/token overrides via `ROLE_PROVIDER_*`,
  `ROLE_MODEL_*`, `ROLE_MAX_TOKENS_*`. No provider imports leak outside the factory.
- The brief's worry that "README references `evals/` but the dir is `eval/`" is **unfounded** —
  README and CLAUDE.md already use `eval/` correctly. Verified: zero `evals/` references.

So Phase 2 is *not* a from-scratch provider refactor. The genuine, in-scope upgrades are
below, re-grounded against the code as it actually exists today.

## 1. Gaps vs. current SOTA agentic patterns

| # | Pattern | Status today | Evidence |
|---|---|---|---|
| G1 | Universal provider abstraction | **Partial** — 3 hardcoded `if provider==` blocks. Adding OpenAI-compatible / Gemini / Bedrock / Mistral needs code edits. No `init_chat_model`. | `agents/llm.py:92-148` |
| G2 | LLM call resilience (retry/timeout/backoff) | **Missing** — no tenacity/backoff. Specialist `agent.invoke()` is unguarded; raw failures bubble up. Worst for flaky local Ollama. | `graph/workflow.py:114`; `requirements.txt` |
| G3 | Agent trajectory + tool-call evals | **Missing** — evals test routing accuracy + final-answer keywords only. No node-sequence or tool-call-correctness checks. | `eval/agent_eval.py` |
| G4 | Agentic RAG (query rewrite, rerank, CRAG/self-RAG grading) | **Missing** — MMR top-k, no relevance grading before use, no query rewrite. | `memory/vector_store.py:117-145` |
| G5 | Structured citations | **Lossy** — filename-only, no chunk IDs or scores. | `memory/vector_store.py:144-145` |
| G6 | Context compaction for long threads | **Missing** — `messages` accumulates unbounded; only *output* token caps exist. Long checkpointed threads will eventually overflow context. | `graph/state.py:16`; `agents/llm.py:34-44` |
| G7 | Critic robustness with small models | **Weak** — structured-output parse failure silently returns `score=1.0, should_revise=False`, masking failures. No retry. | `agents/critic.py:87-95` |
| G8 | Memory consolidation | **Implicit only** — relies on Mem0 built-in dedup; no explicit summarize/prune. | `memory/episodic.py:83-96` |
| G9 | Semantic tool routing | **Missing** — static per-agent tool sets (fine at current tool counts). | `tools/__init__.py:15-17` |
| G10 | Parallel specialist fan-out | **Missing** — strictly sequential supervisor→one-specialist. | `graph/workflow.py:222-277` |
| G11 | Planner / plan-and-execute node | **Missing** — single-shot routing, no decomposition. | `agents/supervisor.py:46-81` |
| G12 | Tool-result caching | **Missing** — no caching of idempotent tool calls. | `tools/*.py` |

## 2. Existing-code weaknesses

- **W1 Critic silent-pass** (G7): parse failure → fake perfect score. Should retry once, then
  fall back to a *neutral* low-confidence decision that doesn't suppress revision.
- **W2 Unguarded LLM calls** (G2): no timeout/retry anywhere; one transient Ollama/Anthropic
  hiccup kills the whole turn.
- **W3 Code-sandbox escape surface**: `tools/code_executor.py` runs user code via `subprocess`
  in `uploads_path` with a wall-clock timeout, but **no import allowlist, no network/FS
  isolation** — `os.system`, sockets, arbitrary installed packages all reachable. E2B noted in
  comments but unimplemented. Acceptable for local single-user; documented risk.
- **W4 MCP loader**: async + multi-transport is good, but **no per-server try/except** (one bad
  server can break load) and the **sync wrapper leaks the client** (never closed).
- **W5 Guardrail coverage**: 11 regex injection patterns cover common jailbreaks but **miss
  encoding attacks** (base64/hex/unicode) and any semantic/multi-turn intent.
- **W6 Dead state**: `retrieved_context` field is declared but never populated — confusing.

## 3. Ranked plan (RICE-style: Impact × Confidence / Effort)

Effort: S<½day, M≈1day, L>1day. Picked tier is what fits this scope without breaking
topology / HITL / checkpointing.

| Rank | Item | Impact | Effort | Verdict |
|---|---|---|---|---|
| **P1** | **G1** Universal `init_chat_model` factory (keep Ollama default + no-key path; any provider via `.env`) | High (mandated) | M | **DO** |
| **P2** | **G2/W2** Resilience wrapper (tenacity retry + timeout) around all LLM `.invoke()` | High | S–M | **DO** |
| **P3** | **G3** Trajectory + tool-call-correctness evals | High (mandated) | M | **DO** |
| **P4** | **G7/W1** Critic small-model robustness (retry + honest fallback) | Med-High | S | **DO** |
| **P5** | **G6** Context compaction (trim/summarize long threads before specialist calls) | Med-High | M | **DO** |
| **P6** | **G4/G5** Agentic RAG: CRAG-lite relevance grading + query rewrite on miss + structured citations (chunk id + score) | High | M–L | **DO (if scope allows)** |
| P7 | **W4** MCP loader hardening (per-server try/except, close sync client) | Med | S | optional add |
| P8 | **G12** Tool-result caching for idempotent tools | Med | S | optional add |
| P9 | **W5** Encoding-aware injection detection | Med | S–M | optional add |
| P10 | **G11** Optional planner node (opt-in via env) | Med | L | **defer** — topology risk |
| P11 | **G10** Parallel fan-out | Med | L | **defer** — needs fan-in + state redesign, HITL risk |
| P12 | **G9** Semantic tool routing | Low | L | **defer** — low ROI at current tool counts |

### Dependency justification (Phase 2 constraint: deps minimal, each justified)
- `tenacity` (P2): tiny, pure-Python, the standard retry lib. Justified by W2.
- `langchain` `init_chat_model` (P1): already transitively present via `langchain-*`; no new
  top-level dep. May add the thin provider extras only when a user selects them.
- P3/P4/P5/P6 use existing deps (langgraph `trim_messages`, langchain core). **No new deps.**

### Constraints honored
- Graph topology, HITL `interrupt()`, and SQLite checkpointing untouched by P1–P4, P7–P9.
- P5/P6 add nodes/logic *within* existing edges, not new branches — topology preserved.
- "Fully local, no API key" stays the documented default path (Ollama).

## 4. Out of scope / deferred (explicit)
Parallel fan-out (P11), planner node (P10), semantic tool routing (P12) — high effort and/or
topology/HITL risk that the brief's "don't break topology" constraint discourages. Documented
as roadmap items rather than implemented blind.
