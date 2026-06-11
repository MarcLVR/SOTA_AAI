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

---

# Phase 2 — Deferred roadmap implementation (June 2026)

> Status: **Implementation plan — GATE. Awaiting approval before any code is written.**
> APIs below were re-verified against the installed venv (langchain 1.3.2, langgraph 1.2.2,
> langchain-core 1.4.0, langgraph-checkpoint 4.1.1) and official docs in June 2026 — not training
> data. Where a fact was confirmed by live introspection/execution it is marked **[verified]**.

Picking up four items previously deferred to the roadmap: SummarizationMiddleware (was C6),
planner + parallel fan-out (P10/P11), memory consolidation (C8), and Docker Compose.

## Phase 1 — `create_agent` + `SummarizationMiddleware`

**Approach.** Attach `SummarizationMiddleware` to each of the 4 specialists (all already
`langchain.agents.create_agent`) and remove the manual `compact_messages()` call from the
specialist node wrapper. The middleware fires `before_model` on every agent step, so compaction
becomes declarative and also covers *intra-turn* tool loops (the hand-rolled node only ran once at
node entry).

- API **[verified]**: `from langchain.agents.middleware import SummarizationMiddleware`;
  `SummarizationMiddleware(model, *, trigger=("tokens"|"messages", N) | list, keep=("messages", N),
  token_counter=..., summary_prompt=..., trim_tokens_to_summarize=4000)`. `create_agent(...,
  middleware=[...])` is the exact kwarg. `trigger=None` ⇒ never summarizes, so we set it explicitly.
- **Ollama constraint [verified]**: `("fraction", f)` requires a model profile that `ChatOllama`
  does not expose (raises `ValueError`). So we use absolute `("messages", N)`/`("tokens", N)` only.
  Summarizer model = the local default `get_llm()` (Ollama-safe, no API key).
- Map existing knobs to the middleware: `trigger=("messages", context_max_messages)`,
  `keep=("messages", context_keep_last)` — behaviour stays equivalent for short threads (no-op until
  the threshold), and triggers on long ones.
- **Critic stays node-level**: the critic is a plain `with_structured_output` call, *not* a
  `create_agent`, so the middleware cannot attach to it **[verified]**. `graph/compaction.py` is
  therefore **kept** (not deleted) and `critic_node` keeps calling `compact_messages()`. This is the
  honest deviation from "delete the old node": full deletion is impossible while the critic isn't an
  agent. The specialist path stops using it.

**Files touched.** `agents/researcher.py`, `agents/coder.py`, `agents/general.py`,
`agents/auditor.py` (add `middleware=[summarizer]`); a new tiny `agents/middleware.py` factory
`build_summarizer()` reading settings; `graph/workflow.py` (`_make_agent_node`: drop the
`compact_messages` call, keep critique injection); `config/settings.py` (no new vars — reuse
`context_max_messages`/`context_keep_last`; add `context_summary_role` optional). `graph/compaction.py`
**unchanged** (still used by critic).

**New deps.** None — `SummarizationMiddleware` ships in the installed `langchain 1.3.2`.

**Rollback.** Revert the 4 builders + `_make_agent_node`; the specialists fall back to the existing
`compact_messages()` call. No state-schema or topology change, so rollback is a clean `git revert`.

## Phase 2 — Plan-and-execute planner + parallel fan-out

**Approach.** Strictly **opt-in** behind `PLANNER_ENABLED` (default `false`) so the shipped default
graph is byte-for-byte the current topology. When enabled, insert a plan-and-execute subgraph
between `guardrail_in` and the specialists:

1. **`planner`** node — `get_llm().with_structured_output(Plan)`. `Plan = {steps: [Step]}`,
   `Step = {description, specialist: researcher|coder|general|auditor, group: int}`. Steps sharing a
   `group` are independent and run in parallel; ascending groups run sequentially.
2. **`dispatch`** node — pops the next group. A conditional edge returns
   `[Send("fanout_worker", {step, ...}) for step in group]` **[verified Send idiom, 1.2.2]**. A
   single-step group still goes through one `Send` (uniform path).
3. **`fanout_worker`** — a *parallel-safe* specialist wrapper. Critical design point: parallel
   branches must **only write reducer-protected keys**. It writes `step_results:
   Annotated[list, operator.add]` and appends to `messages` (which already has the `add_messages`
   reducer — unique message ids make concurrent appends safe). It must **not** write scalar
   `next_agent`/`last_specialist` (concurrent scalar writes raise `InvalidUpdateError` **[verified]**).
4. **`aggregate`** (fan-in) — runs **exactly once** after all branches **[verified]**. Merges
   `step_results` into `past_steps`, composes the merged work into a single AIMessage, and sets the
   scalar routing fields once. Then → `critic` (if enabled) → `replan`.
5. **`replan`** node — `with_structured_output(Act)` where `Act = Response | Plan`. On step failure
   (a worker recorded an error in `step_results`) or remaining steps, emit a revised `Plan`; else
   emit `Response` and route to `hitl`. Capped by `PLANNER_MAX_REPLANS`.

**Composition with critic + HITL [verified safe].** Live test on the installed venv confirmed: an
`interrupt()` in one of several parallel `Send` branches checkpoints and, on resume, **only the
interrupted branch re-runs** — completed specialists are not double-executed. Multi-branch
simultaneous interrupts resume via the `{interrupt_id: value}` map. So HITL composes; the existing
single-response HITL stays, and we keep any side effects after the `interrupt()` call (node re-runs
from its top on resume). The critic runs on the aggregated message, unchanged.

**Files touched.** New `graph/planner.py` (planner/dispatch/aggregate/replan nodes + `Plan`/`Step`/
`Act` Pydantic models + Send fan-out). `graph/state.py` (+ `plan`, `past_steps`
`Annotated[list, operator.add]`, `step_results` `Annotated[list, operator.add]`, `replan_count`).
`graph/workflow.py` (`build_graph`: when `PLANNER_ENABLED`, wire the planner subgraph; else the
current edges verbatim). `config/settings.py` (+ `planner_enabled=False`, `planner_max_replans=2`).
`eval/agent_eval.py` (+ `PlanTestCase`: plan-quality = expected specialists covered; step-adherence
= trajectory contains planned specialists in group order; fan-in-correctness = aggregate runs once &
all `step_results` present). `main.py`/`ui` untouched (graph interface unchanged).

**New deps.** None — `Send`/`Command`/reducers are core langgraph 1.2.2.

**Rollback.** `PLANNER_ENABLED=false` (the default) fully disables it at build time — the planner
nodes are never added to the graph, so the topology is identical to today. Hard rollback = revert
`graph/planner.py` + the guarded block in `build_graph` + the additive state fields (additive, so
old checkpoints still load).

**Risk note.** Highest-risk phase (the original deferral reason). Mitigated by: default-off,
build-time gating (not runtime branching inside the live path), additive-only state, and the
verified interrupt-resume behaviour. If fan-in/interrupt composition misbehaves under the critic, we
ship planner **sequential-only** (skip the `Send` fan-out, run groups serially) as a fallback that
still delivers planning without the parallelism risk.

## Phase 3 — Memory consolidation (LangMem behind the episodic interface)

**Decision: LangMem, introduced as an opt-in backend; Mem0 stays the local default for one release.**
Rationale (fresh search): Graphiti/Zep is **disqualified for the default path** — it mandates a
separate graph-DB server (Neo4j/FalkorDB) and its own docs warn that small local models "frequently
emit JSON that doesn't match the expected schema," causing ingestion failures on the Ollama default.
LangMem is LangChain-native (built on LangGraph `BaseStore`), runs fully local (local LLM + Ollama
embeddings, LangSmith optional), and adds genuine background **consolidation** + procedural memory
over Mem0's per-call extraction. **Caveat:** LangMem is `0.0.30` (Oct 2025, predates langchain 1.x)
— dependency-resolver compatibility against the installed `langchain 1.3.2` / `langgraph 1.2.2` is
**unverified** and is the gating risk. Persistence: there is **no SQLite vector `BaseStore`**
**[verified]**, so we back LangMem with the **existing ChromaDB** via a thin `BaseStore` adapter (no
new infra), not RAM-only `InMemoryStore`.

**Approach.** Refactor `memory/episodic.py` into a pluggable backend behind the *unchanged* public
surface (`add_memory`, `search_memories`, `get_all_memories`, and the `remember`/`recall` tools).
Select via `EPISODIC_BACKEND=mem0|langmem` (**default `mem0`**). Mem0 path is the current code,
untouched. LangMem path wires `create_manage_memory_tool`/`create_search_memory_tool` +
`create_memory_store_manager(get_llm())` over a Chroma-backed store with `OllamaEmbeddings`. Provide
`scripts/migrate_episodic.py` that reads Mem0 `get_all()` facts and re-adds them through the LangMem
backend (best-effort); document that if migration is skipped, switching backends starts memory fresh.

**Gate inside the phase.** First step is a throwaway-venv dry-run install of `langmem==0.0.30`
against the current lockfile. **If it conflicts**, we do *not* force an incompatible pin: we ship the
pluggable-backend refactor with Mem0 as the only wired backend and document LangMem as
"install-and-flag when resolver allows," keeping the abstraction so adoption is a one-flag change
later. This keeps the default path working no matter what.

**Files touched.** `memory/episodic.py` (extract `_Mem0Backend`, add `_LangMemBackend`, dispatch on
setting; public functions/tools unchanged). `config/settings.py` (+ `episodic_backend="mem0"`).
New `scripts/migrate_episodic.py`. `requirements.txt` (+ `langmem` *only if* the dry-run passes,
pinned; otherwise documented as optional). `memory/__init__.py` exports unchanged.

**New deps.** `langmem` (pinned, optional) — justified by consolidation + LangGraph-native
integration; **added only if the compatibility dry-run passes**. No new infra (reuses ChromaDB +
Ollama).

**Rollback.** `EPISODIC_BACKEND=mem0` (default) restores exact current behaviour; the LangMem code
path is dormant unless selected. Revert = drop the `_LangMemBackend` branch.

## Phase 4 — Docker Compose

**Approach.** One-command `docker compose up` bringing up the app + Postgres, with SQLite remaining
the no-docker default. Swap the checkpointer to `langgraph-checkpoint-postgres` **only when
`POSTGRES_URI` is set** (so local/no-docker users are unaffected). Ollama runs **host-mode**
(recommended, GPU) reached via `host.docker.internal:host-gateway` with `OLLAMA_BASE_URL` override
(the app already reads `OLLAMA_BASE_URL`, and `main.py` already binds `0.0.0.0` **[verified]**).

- Checkpointer **[verified]**: long-lived sync `PostgresSaver(ConnectionPool(conninfo=POSTGRES_URI,
  open=True, kwargs={autocommit:True, prepare_threshold:0, row_factory:dict_row}))` + idempotent
  `.setup()` at build — *not* `from_conn_string` (a `@contextmanager` that closes the connection).
  Guarded by `if os.getenv("POSTGRES_URI")`, else the current `SqliteSaver`.
- Compose: `postgres:16` with `pg_isready` healthcheck; app `depends_on: condition:
  service_healthy`; named volumes for `pg_data` and `/app/data` (chroma + mem0 + sqlite checkpoints);
  `env_file: .env`; `extra_hosts: ["host.docker.internal:host-gateway"]`. No obsolete `version:` key.
- Dockerfile: `python:3.11-slim` (3.12+ breaks mem0/chroma), `build-essential`, layer-cached
  `pip install -r requirements.txt`, `EXPOSE 7860`, `CMD ["python","main.py"]`.

**Files touched.** New `docker-compose.yml`, new `Dockerfile`, new `.dockerignore`.
`graph/workflow.py` (checkpointer block → Postgres-when-`POSTGRES_URI`-else-SQLite).
`requirements.txt` (+ `langgraph-checkpoint-postgres==3.1.0`, `psycopg[binary]>=3.2.0`,
`psycopg-pool>=3.2.0`). `config/settings.py` (+ `postgres_uri: str | None = None`). `.env.example`
(+ `POSTGRES_URI` commented). `README.md` (new plain-language "Run with Docker" section + Docker
troubleshooting rows).

**New deps.** `langgraph-checkpoint-postgres==3.1.0` (compatible with installed
`langgraph-checkpoint 4.1.1` **[verified]**), `psycopg[binary]` + `psycopg-pool` (psycopg3 — the
repo only has psycopg2-binary today, used by `chase/db.py`; the checkpointer requires psycopg3).
Justified by the optional Postgres checkpointer; **inert unless `POSTGRES_URI` is set**.

**Rollback.** No `POSTGRES_URI` / no docker ⇒ identical SQLite behaviour. Remove the three new files
+ revert the checkpointer block; the extra deps are unused without `POSTGRES_URI`.

## Cross-cutting verification (run after every phase)

`python -m eval.agent_eval` (routing + e2e + trajectory) and `python -m eval.rag_eval` pass; the app
boots with defaults unchanged (local Ollama, no API key, no docker, SQLite checkpointer); HITL
`interrupt()` + checkpoint resume still work. Commit per phase with a one-line summary.

### Known unverified items (carried forward, to confirm during implementation)
- LangMem `0.0.30` dependency resolution against langchain 1.3.2 / langgraph 1.2.2 (Phase 3 gate).
- `nomic-embed-text` exact dimensionality for the LangMem store `index.dims` (confirm via
  `len(OllamaEmbeddings(...).embed_query("x"))`).
- Host-Ollama reachability from the container requires the host daemon to bind `0.0.0.0`
  (`OLLAMA_HOST=0.0.0.0:11434`) and allow the docker bridge subnet — host-side, not fixable in
  Compose; documented in README troubleshooting.
- The dict-form `trigger` (AND logic) for SummarizationMiddleware is absent from installed 1.3.2 —
  we use list/tuple `ContextSize` forms only.
