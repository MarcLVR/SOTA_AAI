# SOTA Agentic AI

A **state-of-the-art multi-agent system** with a pluggable domain layer.
Showcases the complete modern agentic stack:
LangGraph · Reflexion · HITL · RAG · Episodic Memory · Guardrails · Langfuse.

Supports **Gemini, Groq, and Ollama** (local). No single vendor lock-in.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Gradio UI                              │
│       streaming chat · trace panel · RAG upload · HITL panel   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                ┌────────▼────────┐
                │  guardrail_in   │  prompt injection · PII redaction
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │   Supervisor    │  structured routing · loop guard
                └──┬──┬───┬───┬──┘
                   │  │   │   │
        ┌──────────▼┐ │ ┌─▼─┐ ┌▼──────────────┐ ┌▼──────────────┐
        │ Researcher│ │ │Cdr│ │    General    │ │    Auditor    │
        │  ReAct    │ │ │ReA│ │    ReAct      │ │    ReAct      │
        └─────┬─────┘ │ └─┬─┘ └───────┬───────┘ └──────┬────────┘
              └───────┴───┴───────────┴────────────────┘
                                      │
                             ┌────────▼────────┐
                             │     Critic      │  Reflexion · score → revise or FINISH
                             └────────┬────────┘
                                      │
                             ┌────────▼────────┐
                             │      HITL       │  interrupt() · human approval gate
                             └────────┬────────┘
                                      │
                                    [END]

Tool Layer:  web_search · python_repl (sandboxed) · file_tools · RAG retrieval
             run_full_audit · crawl_repository · extract_document · score_staleness
             check_standards · check_governance · aggregate_findings
Memory:      ChromaDB (semantic) · Mem0 (episodic) · SQLite (conversation state)
Observability: Langfuse traces · ConsoleTracer · RAGAS evals
```

### Deep-dive: how the graph executes

The LangGraph `StateGraph` flows a single `AgentState` TypedDict through every node. All nodes read from and write to this shared state — there are no side-channel calls between agents.

**`guardrail_in`** — runs before the supervisor sees the input. Checks for prompt injection patterns and redacts PII using regex heuristics. If a hard violation is detected, the graph short-circuits to END with a rejection message rather than routing to an agent.

**`supervisor`** — calls the LLM with a structured output schema (`SupervisorDecision`) containing a `next` field (one of `researcher | coder | general | auditor | FINISH`) and a `reasoning` field. A `rounds` counter in state prevents infinite loops: if `rounds >= MAX_SUPERVISOR_ROUNDS`, the supervisor is forced to return `FINISH`.

**Specialist agents** (`researcher`, `coder`, `general`, `auditor`) — all built with `create_react_agent` from `langgraph.prebuilt`. Each has its own tool set and a shared pair of episodic memory tools (`remember`, `recall`). The agents run their internal ReAct loop (observe → think → act) and place an `AIMessage` back on the `messages` list. Execution then moves unconditionally to the critic.

**`critic`** — calls the LLM with `CriticDecision` structured output: `{ score: float, reasoning: str, revise: bool }`. If `score < CRITIC_REVISION_THRESHOLD` (default 0.70) and fewer than `CRITIC_MAX_REVISIONS` (default 2) have been attempted for this turn, the critique is injected as a system hint and routing returns to the originating specialist. Otherwise, execution advances to `hitl`.

**`hitl`** — calls `langgraph.types.interrupt()`. The graph suspends, serialising state to the SQLite checkpointer. The Gradio UI detects the interrupt, surfaces the approval panel, and resumes the graph via `resume_after_hitl(thread_id, approved, feedback)`.

**Checkpointer** — `SqliteSaver` at `data/chroma_db/checkpoints.sqlite`. Every graph step is journaled atomically. Pass the same `thread_id` to resume any prior conversation exactly where it stopped, including across process restarts.

---

## Features

| Feature | Technology |
|---|---|
| Multi-agent orchestration | LangGraph (supervisor pattern) |
| Specialist agents | ReAct (`create_react_agent`) — Researcher, Coder, General, **Auditor** |
| **AI-Readiness Auditor** | Domain pipeline: crawl → extract → staleness/standards/governance → aggregate |
| **Reflexion / self-critique** | Critic node with structured `CriticDecision` scoring (0–1) |
| **Human-in-the-loop (HITL)** | LangGraph `interrupt()` — pause, review, approve or reject |
| **Episodic memory** | Mem0 (Ollama-backed) — remembers facts across sessions |
| **Input/output guardrails** | Prompt injection detection · PII redaction |
| **Observability** | Langfuse cloud tracing + local ConsoleTracer |
| **Eval harness** | RAGAS (answer correctness, faithfulness) + agent evals |
| RAG / semantic memory | ChromaDB + `sentence-transformers` (CPU, no API key) |
| Sandboxed code execution | subprocess + timeout + matplotlib support |
| Tool use | DuckDuckGo web search, Python REPL, File I/O, Office document extraction |
| MCP servers | `langchain-mcp-adapters` → filesystem (experimental) |
| Conversation persistence | SQLite checkpointer (resume by thread ID) |
| LLM providers | **Gemini** (default) · **Groq** (fast, high rate limits) · Ollama (local) |
| UI | Gradio 6.x (streaming, file upload, agent trace panel) |
| Config | `pydantic-settings` + `.env` · per-role provider overrides |

---

## Installation

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.11 exactly** | 3.12+ breaks Mem0 and ChromaDB native deps |
| Git | any | |
| Ollama | latest | Only needed for local LLM or embeddings |
| GOOGLE_API_KEY **or** GROQ_API_KEY | — | At least one LLM provider key is required |

---

### Option A — Windows with WSL2

The app runs inside Ubuntu on WSL2. Ollama (if used) runs natively on Windows and is reachable from WSL2 at `http://localhost:11434` because WSL2 bridges the loopback.

#### Step 1 — Enable WSL2 and install Ubuntu

Open **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu
```

This enables the WSL2 feature, installs the Linux kernel update, and installs Ubuntu 22.04. Restart when prompted. Ubuntu opens automatically on first boot and asks you to create a UNIX username and password.

To verify WSL2 (not WSL1) is active:

```powershell
wsl --list --verbose
# Expected: Ubuntu   Running   2
```

#### Step 2 — (Optional) Install Ollama on Windows

Only needed if you want local LLM (`LLM_PROVIDER=ollama`) or if you need local embeddings without a cloud API key.

Download the Windows installer from [ollama.ai/download](https://ollama.ai/download), run it, then open a normal (non-admin) PowerShell and pull the models:

```powershell
# Chat model
ollama pull llama3.2

# Embedding model — required by both RAG and Mem0 regardless of LLM_PROVIDER
ollama pull nomic-embed-text
```

Ollama runs as a background Windows service after installation. Verify it is up:

```powershell
curl http://localhost:11434/api/tags
# Should return a JSON list of installed models
```

#### Step 3 — Install Python 3.11 inside Ubuntu

Open the Ubuntu terminal:

```bash
sudo apt update && sudo apt upgrade -y

# Install Python 3.11 and build tools
sudo apt install -y \
  python3.11 \
  python3.11-venv \
  python3.11-dev \
  python3-pip \
  git \
  build-essential \
  libssl-dev \
  libffi-dev
```

Confirm the version:

```bash
python3.11 --version
# Python 3.11.x
```

#### Step 4 — Clone the repository

```bash
git clone https://github.com/MarcLVR/SOTA_AAI.git
cd SOTA_AAI
```

#### Step 5 — Create and activate a virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate

# Confirm the right Python is active
python --version   # Python 3.11.x
which python       # .../SOTA_AAI/.venv/bin/python
```

Always run `source .venv/bin/activate` at the start of every new terminal session before running the app.

#### Step 6 — Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

The first install downloads ~1.5 GB including PyTorch (CPU), sentence-transformers, ChromaDB, and all LangChain packages. This takes 3–10 minutes depending on your connection.

Notable heavy packages and why they are needed:

| Package | Size | Purpose |
|---|---|---|
| `torch` (CPU) | ~700 MB | Required by `sentence-transformers` for local embeddings |
| `sentence-transformers` | ~90 MB model download on first run | `all-MiniLM-L6-v2` — local RAG embeddings, no API key |
| `chromadb` | ~30 MB | Persistent vector store for RAG + Mem0 |
| `mem0ai` | ~20 MB | Episodic memory, uses Ollama `nomic-embed-text` locally |

#### Step 7 — Configure the environment

```bash
cp .env.example .env
nano .env   # or use your preferred editor
```

Set **at minimum** one LLM provider key (see [Environment variables reference](#environment-variables-reference) below):

```env
# Option 1 — Gemini (recommended, free tier at aistudio.google.com)
LLM_PROVIDER=gemini
GOOGLE_API_KEY=AIza...

# Option 2 — Groq (free tier, fastest inference)
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...

# Option 3 — Ollama (fully local, no key needed)
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
```

#### Step 8 — Verify the installation

```bash
# Smoke-test: import the core packages
python -c "import langchain, langgraph, chromadb, gradio; print('OK')"

# Run the agent eval suite (no LLM calls, just import checks)
python -m eval.agent_eval --routing
```

#### Step 9 — Run

```bash
source .venv/bin/activate
python main.py
```

Open [http://localhost:7860](http://localhost:7860) in your browser.

For a non-UI smoke test:

```bash
python main.py --query "What is RAG?"
```

---

### Option B — Linux / macOS

```bash
# Install Python 3.11 if not present
# Ubuntu/Debian:
sudo apt install -y python3.11 python3.11-venv python3.11-dev build-essential

# macOS (Homebrew):
brew install python@3.11

# (Optional) Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve &
ollama pull llama3.2
ollama pull nomic-embed-text

# Clone and install
git clone https://github.com/MarcLVR/SOTA_AAI.git
cd SOTA_AAI
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure
cp .env.example .env
# edit .env — set LLM_PROVIDER + the corresponding API key

# Run
python main.py
```

---

## Environment variables reference

All variables are read by `config/settings.py` via `pydantic-settings`. They can be set in `.env` or exported as shell environment variables (shell takes precedence).

### LLM provider

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | Global default provider: `gemini` \| `groq` \| `ollama` |
| `GOOGLE_API_KEY` | — | Required when provider is `gemini`. Get at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `GROQ_API_KEY` | — | Required when provider is `groq`. Get at [console.groq.com/keys](https://console.groq.com/keys) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.2` | Ollama chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model (used by RAG + Mem0) |
| `ANTHROPIC_API_KEY` | — | Optional fallback — not used by default routing |
| `OPENAI_API_KEY` | — | Optional fallback — not used by default routing |

### Per-role provider overrides

Any role can be pinned to a specific provider. The env var pattern is `ROLE_PROVIDER_<ROLE>`:

```env
ROLE_PROVIDER_CRITIC=groq       # critic runs on Groq (low-latency scoring)
ROLE_PROVIDER_AUDITOR=groq      # auditor runs on Groq (high volume)
ROLE_PROVIDER_SUPERVISOR=gemini # supervisor uses Gemini (structured reasoning)
```

Valid roles: `supervisor`, `researcher`, `coder`, `general`, `auditor`, `critic`.

### Agent behaviour

| Variable | Default | Description |
|---|---|---|
| `MAX_SUPERVISOR_ROUNDS` | `5` | Hard cap on supervisor re-routing loops per turn |
| `MAX_ITERATIONS` | `10` | Max ReAct iterations per specialist agent per turn |
| `TEMPERATURE` | `0.0` | Sampling temperature for all LLM calls |
| `CRITIC_REVISION_THRESHOLD` | `0.70` | Critic score below this → send back for revision |
| `CRITIC_MAX_REVISIONS` | `2` | Max revision cycles before forcing FINISH |

### RAG and memory

| Variable | Default | Description |
|---|---|---|
| `CHROMA_PERSIST_DIR` | `./data/chroma_db` | ChromaDB persistence directory |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | HuggingFace embedding model for RAG |
| `TOP_K_RETRIEVAL` | `5` | Number of chunks returned per RAG query |

### Guardrails

| Variable | Default | Description |
|---|---|---|
| `GUARDRAIL_MAX_INPUT_LENGTH` | `8000` | Input length limit in characters before truncation |
| `GUARDRAIL_REDACT_PII` | `true` | Regex-based PII redaction (emails, phone numbers, etc.) |

### Observability

| Variable | Default | Description |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | — | Activates Langfuse cloud tracing when set |
| `LANGFUSE_SECRET_KEY` | — | Required with `LANGFUSE_PUBLIC_KEY` |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse endpoint (override for self-hosted) |
| `LANGCHAIN_API_KEY` | — | Activates LangSmith tracing when set |

### MCP

| Variable | Default | Description |
|---|---|---|
| `MCP_FILESYSTEM_ROOT` | `./data/uploads` | Root directory exposed to filesystem MCP tools |

---

## Agent guide — what to ask each agent

The supervisor routes automatically based on your question.

### Researcher

Best for: finding information, summarising topics, literature reviews, fact-checking.

```
"What are the most cited papers on RAG from 2024?"
"Find recent news about LLaMA model releases"
"What did the paper I uploaded say about model evaluation?"
```

### Coder

Best for: writing and executing Python code, data analysis, plots.

```
"Write a Python function to compute a confusion matrix and plot it"
"Debug this code: [paste your code]"
"Implement a logistic regression with cross-validation using scikit-learn"
```

> Code runs locally in a sandbox — it cannot access the internet or your filesystem
> outside `data/uploads/`.

### General

Best for: reasoning, explanation, writing, analysis, brainstorming.

Uses episodic memory — remembers facts you tell it across sessions.

```
"Explain the difference between RAG and fine-tuning"
"My name is Marc and I work in credit risk — remember this"
"What do you know about me?"
"Help me design the architecture for a document Q&A system"
```

### Auditor

Best for: AI-readiness scanning, data hygiene audits, governance gap analysis.

Runs a deterministic pipeline: crawl → extract (pdf/docx/xlsx/pptx) → score
staleness, template compliance, and governance metadata → aggregate into a
corpus dashboard.

```
"Audit the documents in /path/to/folder for AI-readiness"
"Which files in my SharePoint export need supervision?"
"Check domain/demo_corpus/files and tell me the top offenders"
"Inspect this single contract for governance gaps: contracts/nda_2019.docx"
```

The auditor returns:
- Headline: total documents, total size, % needing supervision, estimated remediation hours
- Staleness: stale/cold counts, oldest document
- Standards: non-standard formats, missing required sections, retired-standard references
- Governance: files with no owner, unclassified files
- Top offenders by trust score with the single worst finding per file

---

## AI-Readiness Auditor — domain layer

The `domain/` package is a standalone auditing engine the Auditor agent calls.
It can also be run directly:

```bash
# Full corpus audit
python -m domain.run_audit /path/to/your/documents

# Machine-readable JSON output
python -m domain.run_audit /path/to/your/documents --json

# Save JSON to file
python -m domain.run_audit /path/to/your/documents --json report.json

# Show top 20 offenders
python -m domain.run_audit /path/to/your/documents --top 20

# Regenerate the 42-file demo corpus and audit it
python -m domain.demo_corpus.generate_sample
python -m domain.run_audit domain/demo_corpus/files
```

### Pipeline internals

`domain/run_audit.py::audit_repository()` runs a pure-Python pipeline with no LLM calls:

```
crawl_repository(folder)
  └─ os.walk + stat → list of {path, name, doc_type, extension, size_bytes, modified_at, accessed_at}

For each file:
  extract_document(path)
    └─ python-docx / openpyxl / python-pptx / pypdf / csv / json / txt
    └─ returns {text, embedded_metadata, extraction_ok}

  score_staleness(doc_type, modified_at, accessed_at, content_text)
    └─ compares age to STALENESS_THRESHOLDS_DAYS per doc type
    └─ checks file access coldness and overdue review dates in text
    └─ returns {staleness_score, is_stale, is_cold, age_days, findings[]}

  check_standards(doc_type, extension, text)
    └─ verifies extension in STANDARD_FORMATS per doc type
    └─ checks required headings from REQUIRED_SECTIONS
    └─ scans text for retired standard references from RETIRED_STANDARDS
    └─ returns {standards_score, is_standard_format, missing_sections[], retired_standard_hits[], findings[]}

  check_governance(doc_type, embedded_metadata, text)
    └─ looks for REQUIRED_METADATA fields in both embedded metadata and inline text
    └─ validates classification labels, owner fields, retention dates
    └─ returns {governance_score, has_owner, classification_valid, missing_fields[], findings[]}

  compute_trust_score(staleness_score, standards_score, governance_score)
    └─ weighted sum per SCORE_WEIGHTS → trust_score in [0, 1]
    └─ trust_score < SUPERVISION_THRESHOLD → needs_supervision = True

aggregate_findings(document_results)
  └─ corpus-level dashboard: headline, staleness summary, standards summary,
     governance summary, reasons breakdown, by_doc_type, top_offenders,
     oldest_5, largest_5, estimated_remediation_hours
```

### Trust score

Each document gets a `trust_score` in [0, 1] computed as a weighted combination:

| Signal | Weight | What it checks |
|---|---|---|
| Staleness | 40% | Age vs type-specific review cadence; access coldness; overdue review dates in body |
| Standards | 35% | Required sections present; standard format (.docx/.xlsx/.pptx/.pdf); retired standard refs |
| Governance | 25% | Owner, classification, retention, review date in metadata or inline |

Documents below **0.70** are flagged for supervision.

### Supported document types

`.pdf` · `.docx` · `.xlsx` · `.pptx` · `.csv` · `.json` · `.txt` · `.md`

Structured exports (Asana JSON, Business Central CSV) are summarised and scored.

### Tuning

Edit `domain/knowledge.py` to adjust:
- `STALENESS_THRESHOLDS_DAYS` — review cadence per doc type
- `RETIRED_STANDARDS` — retired/superseded standard triggers
- `REQUIRED_SECTIONS` — required headings per doc type
- `REQUIRED_METADATA` — governance fields per doc type
- `SCORE_WEIGHTS` — staleness/standards/governance weighting
- `SUPERVISION_THRESHOLD` — trust score cutoff (default 0.70)

---

## Reflexion — how the self-critique loop works

After every specialist responds, the **Critic** node evaluates the response:

1. Scores it 0.0–1.0 using structured output (`CriticDecision`)
2. If score < `CRITIC_REVISION_THRESHOLD` (default 0.70) → injects the critique as a system hint and sends back to the same specialist for revision
3. If score ≥ 0.70 → accepts the response and advances to HITL
4. Maximum `CRITIC_MAX_REVISIONS` (default 2) revision rounds — prevents infinite loops

The critic prompt instructs the LLM to score on: factual accuracy, completeness, relevance, and format. The `CriticDecision.reasoning` field is surfaced in the Gradio trace panel so you can see exactly why a response was revised.

---

## Human-in-the-Loop (HITL)

Toggle **"Enable HITL"** in the UI before sending a message.

1. Agent generates a response; critic scores it
2. Execution pauses via `langgraph.types.interrupt()` — state is checkpointed to SQLite
3. An approval panel appears in the Gradio UI
4. Click **Approve** to accept, or type feedback and click **Reject** to inject the feedback as a new user message and resume

Under the hood, the UI calls `resume_after_hitl(thread_id, approved=True/False, feedback="...")` which calls `graph.update_state()` and then streams the graph continuation from the checkpoint.

---

## Episodic Memory

Tell the agent facts; it remembers them across sessions via Mem0 + Ollama embeddings.

```
"Remember that I prefer concise answers with code examples"
"I am based in Spain and work under EU regulations"
"What do you know about my preferences?"
```

Facts persist in `data/chroma_db/mem0` across restarts.

**How it works:** `memory/episodic.py` exposes two LangChain tools injected into every agent:
- `remember(fact: str)` — calls `mem0.add()` which embeds the fact and stores it in the local Chroma collection
- `recall(query: str)` — calls `mem0.search()` which does a cosine similarity search and returns the top-k relevant facts

Embeddings use Ollama `nomic-embed-text`. If Ollama is not running, episodic memory silently degrades (tools return empty).

---

## Observability

Langfuse tracing is activated when `LANGFUSE_PUBLIC_KEY` is set in `.env`.
LangSmith tracing is activated when `LANGCHAIN_API_KEY` is set.
Without either, all events are logged to the terminal:

```
[supervisor] round=1 → auditor | document audit request
[tool] ▶ run_full_audit({'folder_path': 'domain/demo_corpus/files'})
[tool] ✓ content='{"headline": {"total_documents": 42 ...
[critic] score=0.85 revise=False
```

All callbacks are injected via `observability/tracer.py::get_callbacks()` which is called once at graph build time. The callback list is passed in the `config` dict to every `graph.invoke()` / `graph.stream()` call — no monkey-patching.

---

## Evals

```bash
source .venv/bin/activate

# Routing accuracy + e2e keyword tests (fast, ~20 s)
python -m eval.agent_eval

# Routing only (no LLM calls, import-level check)
python -m eval.agent_eval --routing

# RAGAS metrics — requires a running LLM and Ollama embeddings
python -m eval.rag_eval
```

Results are written to `eval/results/`.

---

## Project structure

```
SOTA_AAI/
├── main.py                      # entrypoint — UI / CLI / single query
├── requirements.txt
├── .env.example
├── config/
│   └── settings.py              # pydantic-settings config singleton
├── agents/
│   ├── llm.py                   # LLM factory — Gemini / Groq / Ollama, per-role override
│   ├── supervisor.py            # structured routing (SupervisorDecision)
│   ├── researcher.py            # web search + RAG ReAct agent
│   ├── coder.py                 # Python REPL ReAct agent
│   ├── general.py               # catch-all reasoning + episodic memory
│   ├── auditor.py               # AI-readiness auditor ReAct agent
│   └── critic.py                # Reflexion critic (CriticDecision)
├── graph/
│   ├── state.py                 # AgentState TypedDict
│   └── workflow.py              # LangGraph builder — full SOTA topology
├── tools/
│   ├── web_search.py
│   ├── code_executor.py
│   ├── file_tools.py
│   └── audit_tools.py           # @tool wrappers for domain audit pipeline
├── domain/                      # AI-readiness auditor domain layer (no LLM calls)
│   ├── knowledge.py             # thresholds, retired standards, required sections
│   ├── prompts.py               # inspector / auditor system prompts
│   ├── run_audit.py             # CLI + programmatic API: python -m domain.run_audit <folder>
│   ├── tools/
│   │   ├── crawler.py           # os.walk + stat inventory
│   │   ├── extractor.py         # pdf/docx/xlsx/pptx/csv/json text + metadata
│   │   ├── staleness.py         # age / cold / overdue-date scoring
│   │   ├── standards.py         # section compliance + retired-ref detection
│   │   ├── governance.py        # owner / classification / retention checks
│   │   └── aggregate.py         # per-doc trust_score + corpus dashboard
│   └── demo_corpus/
│       ├── generate_sample.py   # generates 42-file realistic Office corpus
│       └── files/               # generated .docx/.xlsx/.pptx/.txt/.json/.csv
├── memory/
│   ├── vector_store.py          # ChromaDB + HF embeddings (semantic RAG)
│   └── episodic.py              # Mem0 episodic memory
├── guardrails/
│   └── io_guards.py             # prompt injection detection · PII redaction
├── observability/
│   └── tracer.py                # Langfuse callback + ConsoleTracer
├── eval/
│   ├── agent_eval.py            # routing accuracy + e2e eval harness
│   └── rag_eval.py              # RAGAS metrics
├── mcp_servers/
│   ├── config.py
│   └── loader.py                # MCP tool loader (experimental)
├── ui/
│   └── app.py                   # Gradio 6.x streaming UI
└── data/
    ├── chroma_db/               # persisted vector stores + SQLite checkpoints
    └── uploads/                 # sandboxed file workspace
```

---

## Troubleshooting

**`Command 'python' not found`** — activate the venv: `source .venv/bin/activate`

**`ModuleNotFoundError` after install** — confirm you are using Python 3.11, not 3.12+:
```bash
python --version   # must be 3.11.x
```

**Gemini 429 rate limit** — free tier is 15–20 requests/minute on `gemini-2.5-flash`.
Switch to Groq for high-volume agents:
```env
ROLE_PROVIDER_AUDITOR=groq
ROLE_PROVIDER_CRITIC=groq
```

**Ollama not reachable** — on Windows check the system tray icon; on Linux run `ollama serve`.
Test connectivity from inside WSL2: `curl http://localhost:11434/api/tags`

**Episodic memory fails silently** — Mem0 requires Ollama `nomic-embed-text` to be pulled.
```bash
ollama pull nomic-embed-text
```

**Model outputs are poor / supervisor loops** — use a larger model.
For Ollama: `ollama pull llama3.1:8b` and set `OLLAMA_MODEL=llama3.1:8b`.

**First startup is slow** — `sentence-transformers` downloads `all-MiniLM-L6-v2` (~90 MB) on first run only. Subsequent starts are fast.

**`sqlite3.OperationalError: database is locked`** — a previous run left the checkpointer open. Kill any other `python main.py` processes:
```bash
pkill -f main.py
```

---

## Key design decisions

**Why LangGraph over pure LCEL?**
Explicit state, conditional routing, and cycle support — essential for Reflexion loops
and HITL interrupts that need to pause and resume mid-graph. LCEL chains are DAGs;
LangGraph supports cycles (supervisor → specialist → critic → supervisor again).

**Why a `run_full_audit` tool instead of per-file agent loops?**
A ReAct loop over 42 files would make ~170 LLM calls and hit free-tier rate limits in
seconds. `run_full_audit` runs the deterministic Python pipeline and surfaces a
single compact JSON dashboard — the LLM makes 2 calls total (plan → summarise).

**Why role-based provider routing?**
Supervisor and auditor need strong instruction-following (Gemini); critic needs
sub-second latency (Groq). `ROLE_PROVIDER_<ROLE>` lets each role use its best model
without changing shared config.

**Why local embeddings?**
`sentence-transformers/all-MiniLM-L6-v2` runs on CPU in ~50 ms with no API cost
and no data leaving the machine.

**Why SQLite checkpointer?**
Zero-dependency persistence. Swap to `langgraph-checkpoint-postgres` for production.

**Why `temperature=0.0`?**
Determinism. Structured output schemas (`SupervisorDecision`, `CriticDecision`) parse
reliably at temperature 0. Set `TEMPERATURE=0.3` if you want more creative outputs
from the general agent.

---

## Roadmap

- [ ] Browser agent via Playwright MCP server
- [ ] Docker Compose setup (Ollama + app in one command)
- [ ] Streaming token-by-token output from specialist agents
- [ ] Multi-turn HITL (back-and-forth revision loop)
- [ ] LangGraph Studio visual debugger integration
- [ ] PDF generation for audit reports
- [ ] SharePoint / OneDrive connector for real-corpus audits

---

## License

MIT
