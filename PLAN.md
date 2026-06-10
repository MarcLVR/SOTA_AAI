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

---

# Phase 1b — Currency audit (web-grounded, June 2026)

Verified against **installed** package versions and official docs (not training data). Ground
truth from the venv: `langgraph 1.2.2`, `langchain 1.3.2`, `langchain-core 1.4.0`,
`langchain-anthropic 1.4.4`, `langchain-openai 1.2.2`, `langchain-community 0.4.2`,
`sentence-transformers 5.5.1`. **The repo already runs on the 1.x line** — the
`requirements.txt` floors (`langgraph>=0.2`, `langchain>=0.3`) are misleading, not the runtime.

### Confirmed deprecations in this repo (must flag; fix low-risk ones)

| # | Stale pattern | Evidence (verified locally) | Current replacement | Action |
|---|---|---|---|---|
| C1 | `requirements.txt` floors `langgraph>=0.2`, `langchain>=0.3` | Installed is 1.2.2 / 1.3.2 | Floor at `>=1.0` to prevent a downgrade to pre-1.x APIs | **DO** (safe) |
| C2 | `langgraph.prebuilt.create_react_agent` in all 4 specialists | docstring: *"This function is deprecated in favor of"*; `langchain.agents.create_agent` is present in the install | `from langchain.agents import create_agent` — same `CompiledStateGraph`, `.invoke({"messages":…})` interface; only `prompt=SystemMessage` → `system_prompt=<str>` changes. Removal targeted for 2.0. | **DO** (mechanical, 4 files, topology unaffected) |
| C3 | `langchain_community.embeddings.HuggingFaceEmbeddings` in `memory/vector_store.py` | `@deprecated(since=0.2.2, alternative_import=langchain_huggingface.HuggingFaceEmbeddings)`; `langchain-community` emits *"being sunset / no longer actively maintained"* | `langchain_huggingface.HuggingFaceEmbeddings` (+ add `langchain-huggingface` dep) | **DO** (drop-in) |

### Verified current — NO action (avoid needless churn)

- **`init_chat_model`** (my P1) — confirmed the current recommended provider-agnostic factory in
  June 2026. Caveat I already handle: `num_predict` is Ollama-only, gated per-provider. ✅
- **`StateGraph`, `add_conditional_edges`, `interrupt()`, `SqliteSaver`** — stable in 1.x, not
  deprecated. ✅ Topology stays.
- **`trim_messages`** (my P5) — still a valid primitive; now the *low-level* option (see C6).

### SOTA gaps newly surfaced by research

| # | Gap | Current best practice (2026) | Verdict |
|---|---|---|---|
| C4 | RAG has **no reranker** (my P6 added grading+rewrite+citations but not reranking) | Two-stage retrieval + cross-encoder rerank is now standard (+15–40% precision). Self-hostable/commercial-safe: **`bge-reranker-v2-m3`** via `sentence-transformers` CrossEncoder — **no new heavy dep** (already installed); model downloads at first use | **PROPOSE** — opt-in `RAG_RERANK` (default off to keep the fast/local promise). Highest-ROI RAG gap |
| C5 | Trajectory evals (my P3) are home-grown | **`agentevals`** (LangChain) is the standard: deterministic trajectory-match + LLM-judge, pairs with LangSmith | **PROPOSE** — new dep; my home-grown version works, so this is optional polish |
| C6 | Compaction (my P5) is hand-rolled | `create_agent` + **`SummarizationMiddleware`** makes it declarative | **DEFER** — only worth it as part of a deeper create_agent refactor; my node-level version also covers the critic (not an agent), so keep for uniformity |
| C7 | RAG embeds with `all-MiniLM-L6-v2`; Mem0 uses `nomic-embed-text` (inconsistent) | MiniLM superseded (not deprecated) by `nomic-embed-text-v1.5` / `BGE-M3` (hybrid) | **PROPOSE** — quality + unification; but swapping requires re-embedding existing stores |
| C8 | Mem0 episodic memory, no explicit consolidation | Mem0 fine as default; **Zep** (temporal KG) / **LangMem** (native LangGraph) do consolidation | **DEFER** — not urgent; dep + behavior change |

### Explicitly evaluated and rejected for this app

- **deepagents** (`langchain-ai/deepagents`, v0.6.8) — planning tool + subagents + virtual-FS
  memory. Real and active, but an opinionated harness for **long-horizon** research/coding loops.
  Overkill for a 4-specialist supervisor graph; would fight the existing topology. **Skip**
  (cherry-pick its middleware ideas only — see C5/C6).
- **A2A protocol** (Linux Foundation, 150+ orgs) — for **agent↔agent cross-org/cross-vendor**
  interop; complementary to MCP (agent↔tools), not a replacement. Inside a single LangGraph app,
  internal routing is just graph edges. **Skip** unless exposing/consuming external agents becomes
  a goal. Added to roadmap, not built.

### Revised recommendation — APPROVED scope: "most advanced but operative"
User chose the most-SOTA option that stays operationally reliable. Implementing **C1–C5 + C7**.

- **C1** floors → `>=1.0`. **C2** `create_react_agent` → `langchain.agents.create_agent` (4 files,
  `prompt`→`system_prompt`, topology unaffected). **C3** embeddings → `langchain_huggingface`.
- **C4** reranker: SOTA two-stage retrieval. Operative default = CPU-fast `BAAI/bge-reranker-base`
  (env `RAG_RERANK_MODEL`, overridable to `BAAI/bge-reranker-v2-m3`), lazy-loaded, **graceful
  fallback** to grading-only if unavailable. Toggle `RAG_RERANK` (default on).
- **C5** adopt `agentevals` as the trajectory scoring engine; keep the `subgraphs=True` capture.
- **C7** RAG embeddings `all-MiniLM-L6-v2` → `bge-small-en-v1.5` (384-dim, better quality, still
  CPU-only/no-service). Deliberately NOT unifying on Ollama nomic — keeps RAG service-free so the
  Anthropic path stays operative without Ollama running. Requires wiping/re-ingesting ChromaDB.
- **Defer:** C6 (SummarizationMiddleware — keep uniform node-level compaction that also covers the
  critic), C8 (Zep/LangMem), deepagents, A2A. All on roadmap.
