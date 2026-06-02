# 🤖 SOTA Agentic AI

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

### Option A — Windows with WSL2 ✅ Tested

The app runs inside Ubuntu on WSL2. Ollama (if used) runs natively on Windows.

#### Step 1 — Install WSL2 + Ubuntu

```powershell
wsl --install -d Ubuntu
```

Restart when prompted. After restart Ubuntu opens automatically.

#### Step 2 — (Optional) Install Ollama on Windows

Only needed if you want local LLM (`LLM_PROVIDER=ollama`).
Download from [ollama.ai/download](https://ollama.ai/download), then pull models:

```powershell
ollama pull llama3.2
ollama pull nomic-embed-text   # required for RAG embeddings
```

#### Step 3 — Install Python 3.11 in Ubuntu

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip git
```

> ⚠️ Use Python 3.11 specifically. Python 3.12+ breaks some ML dependencies (Mem0, ChromaDB).

#### Step 4 — Clone and install

```bash
git clone https://github.com/MarcLVR/SOTA_AAI.git
cd SOTA_AAI
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 5 — Configure

```bash
cp .env.example .env
nano .env
```

Minimum required — choose one LLM provider:

```env
# Option 1: Gemini (recommended — free tier, no local GPU needed)
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your-key-from-aistudio.google.com

# Option 2: Groq (free tier, ultra-fast)
LLM_PROVIDER=groq
GROQ_API_KEY=your-key-from-console.groq.com

# Option 3: Ollama (fully local)
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
```

Optional — per-role provider overrides (e.g. fast model for critic):

```env
ROLE_PROVIDER_CRITIC=groq
ROLE_PROVIDER_AUDITOR=groq
```

Optional — Langfuse observability:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

#### Step 6 — Run

```bash
source .venv/bin/activate
python main.py
```

Open [http://localhost:7860](http://localhost:7860) in your browser.

---

### Option B — Linux / macOS

```bash
# (Optional) Install Ollama for local LLM
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve &
ollama pull llama3.2
ollama pull nomic-embed-text

git clone https://github.com/MarcLVR/SOTA_AAI.git
cd SOTA_AAI
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, then:
python main.py
```

---

## Agent guide — what to ask each agent

The supervisor routes automatically based on your question.

### 🔍 Researcher

Best for: finding information, summarising topics, literature reviews, fact-checking.

```
"What are the most cited papers on RAG from 2024?"
"Find recent news about LLaMA model releases"
"What did the paper I uploaded say about model evaluation?"
```

### 💻 Coder

Best for: writing and executing Python code, data analysis, plots.

```
"Write a Python function to compute a confusion matrix and plot it"
"Debug this code: [paste your code]"
"Implement a logistic regression with cross-validation using scikit-learn"
```

> Code runs locally in a sandbox — it cannot access the internet or your filesystem
> outside `data/uploads/`.

### 🧠 General

Best for: reasoning, explanation, writing, analysis, brainstorming.

Uses episodic memory — remembers facts you tell it across sessions.

```
"Explain the difference between RAG and fine-tuning"
"My name is Marc and I work in credit risk — remember this"
"What do you know about me?"
"Help me design the architecture for a document Q&A system"
```

### 📋 Auditor

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

# Regenerate the 42-file demo corpus
python -m domain.demo_corpus.generate_sample
python -m domain.run_audit domain/demo_corpus/files
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
2. If score < 0.70 → injects the critique as a hint and sends back for revision
3. If score ≥ 0.70 → accepts the response and exits to END
4. Maximum 2 revision rounds — prevents infinite loops

---

## Human-in-the-Loop (HITL)

Toggle **"Enable HITL"** in the UI before sending a message.

1. Agent generates a response; critic scores it
2. Execution pauses — an approval panel appears
3. Click **Approve** to accept, or type feedback and click **Reject** to revise

---

## Episodic Memory

Tell the agent facts; it remembers them across sessions via Mem0 + Ollama.

```
"Remember that I prefer concise answers with code examples"
"I am based in Spain and work under EU regulations"
"What do you know about my preferences?"
```

Facts persist in `data/chroma_db/mem0` across restarts.

---

## Observability

Langfuse tracing is activated when `LANGFUSE_PUBLIC_KEY` is set in `.env`.
Without it, all events are logged to the terminal:

```
[supervisor] round=1 → auditor | document audit request
[tool] ▶ run_full_audit({'folder_path': 'domain/demo_corpus/files'})
[tool] ✓ content='{"headline": {"total_documents": 42 ...
[critic] score=0.85 revise=False
```

---

## Evals

```bash
source .venv/bin/activate
python -m eval.agent_eval          # routing accuracy + e2e keyword tests
python -m eval.agent_eval --routing
python -m eval.rag_eval            # RAGAS metrics
```

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
├── domain/                      # AI-readiness auditor domain layer
│   ├── knowledge.py             # thresholds, retired standards, required sections
│   ├── prompts.py               # inspector / auditor system prompts
│   ├── run_audit.py             # CLI: python -m domain.run_audit <folder>
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

**Gemini 429 rate limit** — free tier is 20 req/day on `gemini-2.5-flash`.
Switch to Groq for high-volume agents: `ROLE_PROVIDER_AUDITOR=groq`

**Ollama not reachable** — on Windows check the system tray; on Linux run `ollama serve`.

**Model outputs are poor / supervisor loops** — use a larger model.
For Ollama: `ollama pull llama3.1:8b` and set `OLLAMA_MODEL=llama3.1:8b`.

**First startup is slow** — `sentence-transformers` downloads ~90MB on first run only.

---

## Key design decisions

**Why LangGraph over pure LCEL?**
Explicit state, conditional routing, and cycle support — essential for Reflexion loops
and HITL interrupts that need to pause and resume mid-graph.

**Why a `run_full_audit` tool instead of per-file agent loops?**
A ReAct loop over 42 files would make ~170 LLM calls and hit free-tier rate limits in
seconds. `run_full_audit` runs the deterministic pipeline in Python and surfaces a
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
