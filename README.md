# DAVE — Data Agentic Validation Engine

> **DAVE** audits your document repositories for AI-readiness: it tells you which
> files are stale, missing governance metadata, or referencing retired standards —
> and flags them before they mislead the RAG systems you're building on top of them.

Built on **LangGraph + Claude (Anthropic)**, with a quota-safe Gradio dashboard that
works even when your API key is exhausted. No proprietary platform lock-in.

---

## What problem does this solve?

When organisations build RAG systems on top of their document libraries, the quality
of the retrieved context is only as good as the documents themselves. A contract
from 2018 that references a superseded ISO standard, a policy with no named owner,
or a procedure last reviewed three years ago — all of these will be retrieved by
the RAG system and presented to users as authoritative truth.

DAVE scores every document in your repository on three axes:

| Signal | What it catches |
|---|---|
| **Staleness** | Documents past their type-specific review cadence, files nobody has accessed in months, overdue review dates written in the body text |
| **Standards compliance** | Missing required sections, non-standard file formats, references to retired/superseded standards (e.g. ISO/IEC 27001:2013, "proposed AI Act") |
| **Governance** | No named owner, no data classification, missing retention date or review date in metadata |

Each document gets a **trust score between 0 and 1**. Anything below 0.70 is flagged
for human review before you feed it to a RAG pipeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   GRADIO UI  (http://localhost:7860)                                        │
│   ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌────────┐  │
│   │  🔍 Scan     │  │  📋 Documents    │  │  🤖 Ask the agent│  │📊 Ana- │  │
│   │  Quota-safe  │  │  Drill-in table  │  │  Full multi-agent│  │lytics  │  │
│   │  No LLM      │  │  Row → findings  │  │  with trace panel│  │(DB opt)│  │
│   └──────┬───────┘  └────────┬─────────┘  └──────────────┬───┘  └───┬────┘  │
│          │                   │                            │                 │
└──────────┼───────────────────┼────────────────────────────┼─────────────────┘
           │                   │                            │
           │  audit_repository()                            │  LangGraph graph
           │  (pure Python,                                 │  (stateful, resumable)
           │   no API calls)                                │
           ▼                                                ▼
┌──────────────────────┐              ┌──────────────────────────────────────┐
│   DOMAIN PIPELINE    │              │           LANGGRAPH STATE MACHINE     │
│                      │              │                                        │
│  crawl_repository()  │              │  ┌─────────────┐                      │
│  ↓                   │              │  │ guardrail_in│ PII redaction +       │
│  extract_document()  │              │  │             │ injection detection   │
│  ↓                   │              │  └──────┬──────┘                      │
│  score_staleness()   │              │         │                              │
│  check_standards()   │              │  ┌──────▼──────┐                      │
│  check_governance()  │              │  │  Supervisor │ Routes to the right  │
│  ↓                   │              │  │  (Claude)   │ specialist agent     │
│  compute_trust_score │              │  └──┬───┬───┬──┘                      │
│  ↓                   │              │     │   │   │                          │
│  aggregate_findings()│              │  ┌──▼─┐ │ ┌─▼──┐  ┌────────┐         │
│                      │              │  │Res-│ │ │Aud-│  │General │         │
│  Trust score [0,1]   │◄─────────────┤  │earcheriter│  │        │         │
│  per document        │  tool call   │  │(ReAct)│ │(ReAct)│(ReAct)│         │
└──────────────────────┘              │  └──┬───┘ │ └──┬─┘  └───┬───┘         │
                                      │     └─────┴────┘         │             │
                                      │           │               │             │
                                      │  ┌────────▼───────────────┘            │
                                      │  │       Critic (Groq)                 │
                                      │  │  Scores 0–1 · Reflexion loop        │
                                      │  │  score < 0.70 → revise              │
                                      │  │  score ≥ 0.70 → accept              │
                                      │  └────────┬────────────────────────────│
                                      │           │                             │
                                      │  ┌────────▼────────┐                   │
                                      │  │      HITL       │ Optional human    │
                                      │  │  interrupt()    │ approval gate     │
                                      │  └────────┬────────┘                   │
                                      │           │ END                         │
                                      └───────────┼─────────────────────────────┘
                                                  │
                            ┌─────────────────────┴───────────────────────┐
                            │              SHARED INFRASTRUCTURE           │
                            │                                              │
                            │  Memory:   ChromaDB (RAG) · Mem0 (episodic)│
                            │  LLMs:     Claude Haiku · Claude Sonnet     │
                            │            Groq Llama-3.3 (critic only)     │
                            │  Persist:  SQLite checkpoint (resume conv.) │
                            │  Observe:  Langfuse traces · ConsoleTracer  │
                            └──────────────────────────────────────────────┘
```

---

## How each piece works

### The Domain Pipeline (no LLM, instant, quota-safe)

This is the heart of DAVE. It runs entirely in Python — no API calls, no GPU, no
waiting. You can scan thousands of documents in seconds.

```
1. crawl_repository(folder)
      Walk the folder recursively, collect every file's path, size, and
      last-modified / last-accessed timestamps. Classify each file by doc type
      (contract, policy, procedure, OKR, data dictionary…) from its filename.

2. extract_document(path)
      Read the file content and embedded metadata (author, title, created date)
      using python-docx, openpyxl, python-pptx, pypdf, or plain text parsers.
      Runs in a worker thread with a 10-second timeout — corrupt or encrypted
      files fail fast instead of hanging the pipeline.

3. score_staleness(doc_type, modified_at, accessed_at, text)
      Compare the document's age to the type-specific review cadence defined in
      domain/knowledge.py. A policy is stale after 365 days; a contract after 730.
      Also scans the body text for overdue review dates ("next review: Q1 2022").

4. check_standards(doc_type, extension, text)
      Verify the file uses a standard format (.docx not .txt for contracts).
      Check that required headings (e.g. "Owner", "Scope") are present.
      Scan for references to retired standards (ISO/IEC 27001:2013,
      "proposed AI Act", deprecated regulation names).
      Guard: if a PDF/DOCX/PPTX yields no extractable text, the document is
      flagged as "may be a scanned image or have a text-rendering issue" — it
      cannot be content-audited and always requires manual review.

5. check_governance(doc_type, metadata, text)
      Look for owner field, classification label, retention date, and review date
      in both embedded metadata (Word properties) and inline body text.

6. compute_trust_score()
      Weighted sum: Staleness × 0.40 + Standards × 0.35 + Governance × 0.25
      Result ∈ [0, 1]. Below 0.70 → flagged for supervision.

7. aggregate_findings()
      Roll all per-document results into a corpus dashboard: headline metrics,
      reasons breakdown, by-type breakdown, top offenders, oldest/largest docs.
```

### The LangGraph State Machine

Every user message flows through a directed graph of nodes. All nodes read from and
write to a single `AgentState` TypedDict — there is no shared mutable state
outside the graph.

**`guardrail_in`** — The first gate. Checks for prompt injection patterns
(instructions disguised as user messages) and redacts PII (email addresses, phone
numbers, ID numbers) before the supervisor ever sees the input. Hard violations
short-circuit to END immediately.

**`supervisor` (Claude Haiku)** — Reads the full conversation and returns a
structured routing decision (`SupervisorDecision`) with two fields: `next` (which
agent to call) and `reasoning` (one sentence explaining why). A hard counter stops
infinite loops if `MAX_SUPERVISOR_ROUNDS` is reached.

**Specialist agents** — Each is a `create_react_agent` (LangGraph's ReAct
implementation). The agent receives the conversation history plus its own system
prompt, picks tools to call, runs them, observes the results, and decides whether to
call another tool or give a final answer. This loop continues until the agent
produces a final `AIMessage`.

| Agent | Model | Tools |
|---|---|---|
| **Researcher** | Claude Haiku | `web_search`, `retrieve_from_memory`, `read_file` |
| **Coder** | Claude Haiku | `python_repl` (sandboxed subprocess), `read_file`, `write_file` |
| **General** | Claude Haiku | `remember`, `recall` (episodic memory) |
| **Auditor** | Claude Haiku | `run_full_audit`, `extract_document`, `score_staleness`, `check_standards`, `check_governance` |

**`critic` (Groq Llama-3.3-70B)** — After every specialist responds, the critic
scores the response 0–1 on four axes: completeness, accuracy, clarity, and tool use.
If the score is below 0.70 and fewer than 2 revisions have been attempted, the
critique is injected as a follow-up human message and execution returns to the
specialist. This is the **Reflexion** pattern.

> Groq is used here specifically because it is fast (sub-second) and the critic task
> does not require the reasoning depth of Claude. This saves both latency and cost.

**`hitl`** — When "Enable HITL" is toggled in the UI, execution pauses here using
LangGraph's `interrupt()`. The full graph state is serialised to SQLite. The UI
shows the agent's proposed response and a score. You can approve or reject with
feedback, and the graph resumes from the exact checkpoint.

### Memory

**Semantic / RAG** — Documents you upload in the UI are chunked and embedded using
`sentence-transformers/all-MiniLM-L6-v2` (runs on CPU, no API key, ~50 ms per
query). Stored in ChromaDB locally. The Researcher retrieves relevant chunks before
searching the web.

**Episodic** — Backed by Mem0 with local Ollama embeddings. When you tell the
General agent "remember that I prefer bullet points", it stores that fact. Future
conversations retrieve it via cosine similarity. Persists across restarts.

**Conversation state** — Every graph step is journaled to a SQLite checkpoint. Pass
the same `thread_id` to resume any conversation from where it left off, even after
a process restart.

### LLM provider routing

```
Default provider: Anthropic (claude-haiku-4-5-20251001)

Role overrides (in .env):
  ROLE_PROVIDER_CRITIC=groq          → Groq for speed/cost on 0-1 scoring
  ROLE_MODEL_EXECUTOR=claude-sonnet-4-6  → Sonnet for document rewriting quality

Pattern: ROLE_PROVIDER_<ROLE>=<provider>
         ROLE_MODEL_<ROLE>=<model>
```

---

## Features at a glance

| Feature | Technology | Why it matters |
|---|---|---|
| **AI-readiness audit pipeline** | Pure Python, no LLM | Runs instantly on thousands of docs; survives API quota exhaustion |
| **Scanned PDF detection** | Empty-text guard in `check_standards` | Image-only PDFs are flagged for manual review instead of passing clean |
| **Metadata consistency check** | Deterministic regex, zero LLM | Flags stale author fields, never-revised docs, body-date vs metadata skew |
| **Extraction timeout** | `concurrent.futures`, 10 s | Corrupt or encrypted files fail fast; never hang the pipeline |
| **Semantic compliance layer** | ChromaDB + Claude Haiku (on demand) | Compares a document against company standards; one LLM call per click, `SEMANTIC_ENABLED=true` |
| **Multi-agent orchestration** | LangGraph supervisor pattern | Clean separation of concerns; each agent only does one thing well |
| **Reflexion self-critique** | Critic node, off by default (`CRITIC_ENABLED`) | Optional — enables iterative improvement; off saves one LLM call per answer |
| **Per-role token caps** | `max_tokens` set per role | supervisor=512, auditor=1500, general=2000 — prevents 64k output billing |
| **Human-in-the-loop** | LangGraph `interrupt()` | Pause any response for review; resume from checkpoint without re-running |
| **4-tab Gradio dashboard** | Gradio 6.x + Soft theme | Scan · Documents · Ask the agent · Analytics (DB-optional) |
| **Analytics dashboard** | Postgres + Plotly + ROI hero | KPI cards, severity/status/owner charts, savings estimate; disabled by default |
| **Company standards library** | `standards/*.md` | 9 markdown docs; Researcher cites them; semantic layer compares docs against them |
| **Owner routing** | Author metadata → team owner | Findings routed to the real owner extracted from document metadata |
| **Episodic memory** | Mem0 + local embeddings | Remembers facts across sessions without any cloud dependency |
| **RAG** | ChromaDB + sentence-transformers | CPU-only, no API key, no data leaves the machine |
| **Input guardrails** | Regex + heuristics | Blocks prompt injection and redacts PII before the LLM sees it |
| **Full observability** | Langfuse + ConsoleTracer | Every agent decision, tool call, token count, and score is traceable |
| **Sandboxed code execution** | subprocess + timeout | Coder agent runs Python safely with matplotlib support |
| **Multi-provider LLM** | Anthropic · Groq · Ollama | Per-role overrides; no single vendor dependency |
| **Conversation persistence** | SQLite checkpointer | Resume any conversation across restarts |
| **Docker** | `python:3.11-slim` + uv | Single `docker build` → `docker run`; no host Python needed |

---

## Installation

> You will need: a terminal, Python 3.11, and an Anthropic API key. That's it.
> Estimated setup time: **15–20 minutes** on a standard laptop.

### Before you start — get your API keys

You need at least one of these. Anthropic is the default and recommended choice.

**Anthropic (recommended)**
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Sign up or log in
3. Click **API Keys** in the left sidebar
4. Click **Create Key**, give it a name, and copy the key — it starts with `sk-ant-`
5. Keep it somewhere safe, you'll paste it in Step 7 below

**Groq (free, needed for the critic)**
1. Go to [console.groq.com](https://console.groq.com)
2. Sign up (free, no credit card needed)
3. Click **API Keys** → **Create API Key**
4. Copy the key — it starts with `gsk_`

---

### Option A — Windows (recommended path)

The app runs inside Ubuntu on Windows Subsystem for Linux (WSL2). This takes a
few extra steps but gives you a proper Linux environment on Windows.

#### Step 1 — Open PowerShell as Administrator

Press `Windows key`, type `PowerShell`, right-click **Windows PowerShell**, and
choose **Run as administrator**.

#### Step 2 — Install WSL2 with Ubuntu

Paste this command and press Enter:

```powershell
wsl --install -d Ubuntu
```

This will take a few minutes. When it finishes, **restart your computer**.

After restarting, Ubuntu will open automatically. It will ask you to create a
username and password — choose anything you like, you'll need them later.

> If Ubuntu doesn't open automatically after restart, search for "Ubuntu" in the
> Start Menu and open it.

To confirm everything worked, open a new PowerShell and run:

```powershell
wsl --list --verbose
```

You should see `Ubuntu   Running   2` (the `2` means WSL2, not the older WSL1).

#### Step 3 — Install Python 3.11 in Ubuntu

Open the Ubuntu terminal (search "Ubuntu" in Start Menu). Run these commands one
at a time, pressing Enter after each:

```bash
sudo apt update && sudo apt upgrade -y
```

_(This may take a few minutes. Enter your Ubuntu password when asked.)_

```bash
sudo apt install -y python3.11 python3.11-venv python3.11-dev git build-essential
```

Check it worked:

```bash
python3.11 --version
```

You should see `Python 3.11.x`. If you see 3.12 or higher, see Troubleshooting.

#### Step 4 — Clone the repository

In the Ubuntu terminal:

```bash
git clone https://github.com/OAPMaaS/dave.git
cd dave
```

#### Step 5 — Create a virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Your terminal prompt should now start with `(.venv)`. This means the virtual
environment is active. You need to run `source .venv/bin/activate` every time
you open a new terminal window.

#### Step 6 — Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This downloads about 1.5 GB of packages (PyTorch, LangChain, ChromaDB, etc.).
It takes **5–15 minutes** depending on your internet connection.

> It's normal to see warnings during this step. Only worry about lines that say
> `ERROR`. If you see any ERROR lines, see Troubleshooting below.

#### Step 7 — Configure your API keys

```bash
cp .env.example .env
nano .env
```

This opens a text editor. Use the arrow keys to navigate.

Find these lines and fill in your keys:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...your-key-here...

GROQ_API_KEY=gsk_...your-key-here...
```

To save: press `Ctrl+X`, then `Y`, then `Enter`.

#### Step 8 — Smoke test

```bash
python -c "import langchain, langgraph, chromadb, gradio; print('All good!')"
```

You should see `All good!`. If you see an error, see Troubleshooting.

#### Step 9 — Generate the demo corpus

```bash
python -m domain.demo_corpus.generate_sample
```

This generates 42 synthetic Office documents in `domain/demo_corpus/files/` with
deliberate compliance violations — stale dates, missing owners, retired standards
references, etc. This is what you'll audit in the demo.

#### Step 10 — Launch DAVE

```bash
python main.py
```

Open your **Windows browser** (Chrome, Edge, Firefox) and go to:

**[http://localhost:7860](http://localhost:7860)**

You should see the DAVE dashboard with three tabs.

---

### Option B — macOS

```bash
# Install Python 3.11
brew install python@3.11

# Clone and install
git clone https://github.com/OAPMaaS/dave.git
cd dave
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY and GROQ_API_KEY

# Generate demo corpus and run
python -m domain.demo_corpus.generate_sample
python main.py
```

### Option C — Linux

```bash
# Ubuntu / Debian
sudo apt install -y python3.11 python3.11-venv python3.11-dev build-essential

git clone https://github.com/OAPMaaS/dave.git
cd dave
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your keys
python -m domain.demo_corpus.generate_sample
python main.py
```

### Option D — Docker

No Python installation required on the host.

```bash
git clone https://github.com/OAPMaaS/dave.git
cd dave
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and GROQ_API_KEY

docker build -t dave .
docker run -p 7860:7860 --env-file .env dave
```

Open [http://localhost:7860](http://localhost:7860).

> The image uses `python:3.11-slim` + `uv` for fast dependency installation.
> Secrets are excluded from the image via `.dockerignore`.

---

## Environment variables reference

All variables are read from `.env` by `config/settings.py`. Shell environment
variables override `.env` values.

### LLM providers

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | Active provider: `anthropic` · `groq` · `ollama` |
| `ANTHROPIC_API_KEY` | — | Required. Get at [console.anthropic.com](https://console.anthropic.com) |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Default model for all Anthropic roles |
| `GROQ_API_KEY` | — | Required for the critic. Get at [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server (local LLM) |
| `OLLAMA_MODEL` | `llama3.2` | Ollama chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model (RAG + Mem0) |

### Per-role overrides

Pin any role to a specific provider or model:

```env
ROLE_PROVIDER_CRITIC=groq               # fast scoring — no need to pay Anthropic
ROLE_PROVIDER_SUPERVISOR=anthropic      # strong structured output
ROLE_MODEL_EXECUTOR=claude-sonnet-4-6  # stronger model for document rewriting
```

Valid roles: `supervisor` `researcher` `coder` `general` `auditor` `critic` `executor`

### Agent behaviour

| Variable | Default | Description |
|---|---|---|
| `MAX_SUPERVISOR_ROUNDS` | `5` | Hard cap on routing loops per turn |
| `TEMPERATURE` | `0.0` | Deterministic outputs — reliable structured JSON |
| `CRITIC_ENABLED` | `false` | Set `true` to enable the Reflexion self-critique loop. Off by default — saves one LLM call per answer. |
| `CRITIC_REVISION_THRESHOLD` | `0.70` | Score below this triggers a revision pass (only when `CRITIC_ENABLED=true`) |
| `CRITIC_MAX_REVISIONS` | `2` | Maximum revision rounds per turn |

### LLM output token caps

Per-role `max_tokens` caps prevent the model-default of 64 000 from being billed on every call. Override via `ROLE_MAX_TOKENS_<ROLE>=N`.

| Role | Default cap | Rationale |
|---|---|---|
| `supervisor` | 512 | Routing JSON only |
| `critic` | 512 | Score + one-sentence critique |
| `auditor` | 1 500 | Audit summary |
| `semantic` | 1 000 | 3 compliance verdicts |
| `general` / `researcher` / `coder` | 2 000 | Chat responses |

### Observability

| Variable | Notes |
|---|---|
| `LANGFUSE_PUBLIC_KEY` | Activates Langfuse cloud tracing when set |
| `LANGFUSE_SECRET_KEY` | Required with public key |
| `LANGFUSE_HOST` | Default: `https://cloud.langfuse.com` |
| `LANGCHAIN_API_KEY` | Activates LangSmith tracing when set |

### Semantic compliance

| Variable | Default | Notes |
|---|---|---|
| `SEMANTIC_ENABLED` | `false` | Set `true` to enable the "Check against company standards" button in the Documents tab. Requires ChromaDB loaded with standards (`python -m scripts.load_standards`). |

### Analytics & database

| Variable | Default | Notes |
|---|---|---|
| `DB_ENABLED` | `false` | Set `true` to activate the Analytics tab and Postgres writes. When `false`, the tab shows a "not connected" card and makes zero DB calls — safe for laptop demos. |
| `DB_HOST` | `localhost` | PostgreSQL host (`postgres` only resolves inside Docker) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `dave` | Database name |
| `DB_USER` | `dave` | Database user |
| `DB_PASSWORD` | — | Database password |

### Deployment

| Variable | Default | Notes |
|---|---|---|
| `GRADIO_ROOT_PATH` | `` (empty) | Set to a subpath (e.g. `/dave`) when running behind a reverse proxy |

---

## Demo guide

> This section walks you through a compelling 10-minute demo. Follow the acts in
> order — each one builds on the previous.

### Pre-demo checklist

Run these before your audience arrives:

```bash
# 1. Make sure the demo corpus is generated
ls domain/demo_corpus/files/ | wc -l
# Should print 42

# 2. Start the app and confirm it loads
source .venv/bin/activate
python main.py
# Open http://localhost:7860 — you should see all 4 tabs

# 3. Run a quick smoke test
python main.py --query "Hello"
# Should respond without errors
```

Keep the browser open on **Tab 1 (🔍 Scan)** when your audience arrives.

---

### Act 1 — The instant audit (Tab 1: Scan) ⏱ 2 min

**What to say:** *"This is the dashboard. It audits your entire document repository
in seconds — no AI, no API calls, no quotas. Just fast Python."*

1. The folder path field already shows `domain/demo_corpus/files`
2. Click **Run Audit**
3. In about 1 second, the hero number appears — a large coloured percentage

**What to point out:**
- The big percentage (e.g. "45%") is the share of documents that need human review
  before going into a RAG pipeline
- The stat cards show total documents, total size, flagged size, and estimated
  remediation time in hours
- The **Top reasons bar chart** on the left shows the breakdown: governance gaps
  dominate, then staleness, then standards issues
- The **By document type** chart on the right shows which types have the most
  problems (contracts and data dictionaries tend to be worst)

4. Check the **Extrapolate toggle** — watch the numbers multiply by 30,000
5. **What to say:** *"This sample has 42 documents. If your full SharePoint has
   30,000 documents and the same pattern holds, this is what you're dealing with."*

**Live file upload demo (optional):**
Drag a PDF from your desktop onto the upload zone. The label below updates to
`📄 Will audit 1 uploaded file(s): your-file.pdf`. Click **Run Audit** — DAVE
audits only that file. A scanned / image-only PDF will be flagged with:
*"No text extracted — document may be a scanned image or have a text-rendering
issue. Content cannot be automatically audited; manual review required."*

---

### Act 2 — Document drill-in (Tab 2: Documents) ⏱ 2 min

**What to say:** *"Now let's see exactly which documents are causing the problems."*

1. Click the **📋 Documents** tab
2. The table shows all 42 documents sorted by trust score — worst at the top
3. The first row has a 🔴 badge — click on it

**What to point out in the detail panel:**
- The **Staleness findings** section shows exactly why it's stale, with the age in
  days and the expected review cadence for that document type
- The **Standards findings** section shows the specific retired standard references
  found in the body text
- The **Governance findings** section lists missing metadata fields
- The sub-scores at the bottom show the raw 0–1 values for each dimension

4. Try clicking a row with a 🟡 badge to show the contrast — not terrible, but
   fixable
5. Try clicking a row with a 🟢 badge — a clean document as a baseline

---

### Act 3 — Ask the AI (Tab 3: Ask the agent) ⏱ 4 min

**What to say:** *"The dashboard gives you the raw data. Now let's ask the AI what
to actually do with it."*

Click the **🤖 Ask the agent** tab. Use these prompts in order:

**Prompt 1 — The audit question:**
```
Audit the documents in domain/demo_corpus/files and tell me which 3 issues
to fix first if I want to safely use this corpus in a RAG system
```

Watch the trace panel on the right as you wait:
- `🔀 Supervisor → auditor` — the AI routes to the right specialist
- `🔧 run_full_audit` — the audit tool runs (the same pipeline from Tab 1)
- `✅ Critic score=XX%` — the critic evaluates the response quality

**What to say while it runs:** *"Notice the trace panel. You can see the AI routing
decision and its reasoning. The critic on the right is a separate AI that scores
the answer before it reaches you — this is Reflexion, a technique from a 2023
Stanford paper."*

**Prompt 2 — Memory:**
```
My company's biggest concern is GDPR compliance. Remember this.
```

Then immediately:
```
Given what you know about my priorities, which document in the corpus
is the most dangerous for us right now?
```

**What to say:** *"It remembered the context from the previous message and applied
it to a new question. This works across sessions — close the browser and come back
tomorrow, and it still knows your priorities."*

**Prompt 3 — Research (optional, if time permits):**
```
Search for the current EU AI Act requirements for high-risk AI systems
and tell me which of our compliance documents might need updating
```

Watch the researcher agent run `web_search` multiple times, then synthesise
a response with cited sources.

---

### Act 4 — Show the Reflexion loop ⏱ 1 min

> The critic is **off by default** (`CRITIC_ENABLED=false`) to keep demos fast.
> Enable it for this act by setting `CRITIC_ENABLED=true` in `.env` and restarting.

With the critic enabled, every specialist answer is scored before reaching the user.
If you see a `🔁` in the trace panel:

**What to say:** *"See the 🔁? The critic scored the first response below 0.70 —
not good enough. It sent the agent back with specific feedback. The agent tried
again. This is why the answers are higher quality than a basic chatbot — and it's
only one extra Groq call (fast, near-zero cost)."*

Trigger it with a deliberately vague question:
```
Summarise everything
```

---

### Act 5 — Human-in-the-loop (optional, impressive for governance audiences) ⏱ 1 min

1. Tick the **🔒 Enable human-in-the-loop review** checkbox in the right panel
2. Send any message
3. When the approval panel appears, point it out

**What to say:** *"In a production governance workflow, no AI response goes to a
user without a human seeing it first. The graph literally pauses — the state is
checkpointed to disk — and waits for your approval. If you reject it with
feedback, the agent revises and comes back."*

4. Click **Reject** and type `Make it shorter`
5. Watch the agent revise
6. Click **Approve**

---

### Demo recovery — if something goes wrong

| Problem | Quick fix |
|---|---|
| Blank page at localhost:7860 | Wait 20 seconds — the first startup is slow while the embedding model loads |
| "Error: ANTHROPIC_API_KEY not set" | Check `.env` has the key; run `source .venv/bin/activate` first |
| Audit returns 0 documents | Run `python -m domain.demo_corpus.generate_sample` first |
| Charts stuck on "processing" | This was a `gr.Plot + None` bug — fixed in current version. Run `git pull` |
| Agent gives no response | Check the terminal for error messages; most common cause is a missing API key |
| Semantic button returns nothing | Run a folder scan first, then click the button — it needs `docs_state` to be populated |
| "Multiple system messages" error | Ensure you're on the latest commit: `git pull && python main.py` |
| Reflexion loop not showing | Critic is off by default. Set `CRITIC_ENABLED=true` in `.env` and restart |

---

## Auditor CLI — running outside the UI

The audit pipeline works standalone, no UI needed:

```bash
# Audit any folder
python -m domain.run_audit /path/to/your/documents

# JSON output (pipe to jq, save to file, send to an API)
python -m domain.run_audit /path/to/your/documents --json

# Show top 20 worst documents
python -m domain.run_audit /path/to/your/documents --top 20

# Regenerate the 42-file demo corpus
python -m domain.demo_corpus.generate_sample
```

### Single-document inspector

Inspect one file in full detail — useful for judging finding quality before a demo:

```bash
# Full terminal report (text preview, all findings, adapter output)
python -m domain.inspect_doc path/to/document.docx

# Skip the extracted-text section
python -m domain.inspect_doc path/to/document.docx --no-text

# Machine-readable — pipe to jq
python -m domain.inspect_doc path/to/document.pdf --json | jq '.findings'
```

The report shows: document metadata, extraction status, the three sub-scores with
visual bars, every finding from staleness / standards / governance, and the full
chase-format adapter output (rule_code, severity, title, location, suggestion).

> **Synthetic data:** All documents in `domain/demo_corpus/` and `test_docs/` are
> entirely synthetic, generated by `domain/demo_corpus/generate_sample.py` and
> `scripts/generate_synthetic.py`. They contain no real personal data.

### Trust score weights

| Signal | Weight | What it measures |
|---|---|---|
| Staleness | 40% | Age vs type-specific review cadence; cold access; overdue review dates in body |
| Standards | 35% | Required headings; standard file format; retired standard references; empty-text detection |
| Governance | 25% | Owner field; classification label; retention date; review date |

Documents below **0.70** are flagged. Adjust in `domain/knowledge.py`.

### Metadata consistency check (bonus signal, zero LLM)

`domain/tools/metadata_consistency.py` runs deterministically alongside the three main signals. It detects:
- **Author placeholder** — missing, blank, or generic (e.g. `"Administrator"`)
- **Never revised** — `CreationDate == ModDate` and age ≥ 90 days
- **Body-date skew** — a date in the document body differs from `CreationDate`/`ModDate` by more than 5 days
- **Version string** — extracts version numbers for display (informational)

### Semantic compliance layer (on-demand, one LLM call)

When `SEMANTIC_ENABLED=true`, the Documents tab shows a **"Check against company standards"** button. Clicking it:
1. Embeds the selected document's text locally (sentence-transformers, free)
2. Retrieves the top-3 closest standards from ChromaDB
3. Makes **one Claude Haiku call** (~$0.001) returning a JSON compliance verdict
4. Displays: standard matched, compliance (`compliant` / `partial` / `violation`), detail, severity

Load the standards first: `python -m scripts.load_standards`

---

## DAVE notification pipeline

The Telegram notification pipeline is active when `TELEGRAM_BOT_TOKEN` is set.
`main.py` auto-starts both the bot and the notifier daemon on launch.

### `chase/` — Telegram notification layer

When a finding is detected, `notifier.py` sends the document owner a Telegram
message with four inline buttons: **Fix** (triggers the executor ReAct loop),
**Manual** (marks for human action), **Ignore**, and **Info** (shows full findings).

The notifier daemon (`start_notifier_daemon()`) polls the database for unflagged
runs and sends notifications automatically — no manual trigger needed.

```bash
# Run the bot standalone (useful for testing)
python chase/telegram_bot.py
```

Required env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_<NAME>_CHAT_ID` for each owner,
`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_ENABLED=true`.

Additional install: `pip install python-telegram-bot psycopg2-binary`

### `agents/executor.py` — ReAct auto-fix loop

When an owner approves a fix via Telegram, the executor runs up to 3 ReAct
iterations per finding (Thought → Action → Observation), using Claude Sonnet
(`ROLE_MODEL_EXECUTOR=claude-sonnet-4-6`) for quality document rewriting.

### `scripts/` — utilities

```bash
# Generate the 8-document synthetic test corpus with known violations
python scripts/generate_synthetic.py

# End-to-end pipeline smoke test: connector → PII critic → DB → notifier
python scripts/test_pipeline.py [optional/path/to/doc.docx]

# Export all validation runs and findings from PostgreSQL to JSON
python scripts/export_results.py
```

---

## Project structure

```
dave/
├── main.py                      # entry point — UI / CLI / single query
├── requirements.txt
├── .env.example                 # copy to .env and fill in your keys
├── Dockerfile                   # python:3.11-slim + uv; EXPOSE 7860
├── .dockerignore
│
├── config/
│   └── settings.py              # all env vars → pydantic Settings object
│
├── agents/
│   ├── llm.py                   # LLM factory: Anthropic / Groq / Ollama, per-role routing
│   ├── supervisor.py            # routes conversation to the right specialist
│   ├── researcher.py            # web search + RAG agent
│   ├── coder.py                 # sandboxed Python REPL agent
│   ├── general.py               # reasoning + episodic memory agent
│   ├── auditor.py               # calls domain pipeline, summarises findings
│   ├── critic.py                # Reflexion scorer (0–1, structured output)
│   ├── connector.py             # [standalone] multi-format doc parser
│   └── executor.py              # [standalone] ReAct auto-fix loop
│
├── graph/
│   ├── state.py                 # AgentState TypedDict (shared by all nodes)
│   └── workflow.py              # LangGraph builder + node factory
│
├── tools/
│   ├── web_search.py            # DuckDuckGo search
│   ├── code_executor.py         # sandboxed subprocess Python REPL
│   ├── file_tools.py            # read / write / list
│   └── audit_tools.py           # @tool wrappers for the domain pipeline
│
├── domain/                      # AI-readiness audit engine (zero LLM calls)
│   ├── knowledge.py             # review cadences, retired standards, required sections
│   ├── run_audit.py             # CLI + programmatic API
│   ├── inspect_doc.py           # single-document inspection CLI (--no-text, --json)
│   ├── adapter.py               # audit output → chase/notifier+db format
│   ├── tools/
│   │   ├── crawler.py           # os.walk + stat inventory
│   │   ├── extractor.py         # text + metadata extraction (10s timeout)
│   │   ├── staleness.py         # age / cold / overdue scoring
│   │   ├── standards.py         # section compliance, retired-ref, empty-text guard
│   │   ├── governance.py        # owner / classification / retention checks
│   │   └── aggregate.py         # trust_score + corpus dashboard
│   └── demo_corpus/
│       ├── generate_sample.py   # generates 42-file synthetic corpus
│       └── files/               # generated documents (gitignored after generation)
│
├── standards/                   # Company RAG standards (6 markdown docs)
│   ├── document_lifecycle.md
│   ├── governance_and_pii.md
│   ├── naming_and_structure.md
│   ├── required_sections.md
│   ├── retired_standards.md
│   └── trust_scoring.md
│
├── memory/
│   ├── vector_store.py          # ChromaDB + HuggingFace embeddings
│   └── episodic.py              # Mem0 episodic memory
│
├── guardrails/
│   └── io_guards.py             # injection detection + PII redaction
│
├── observability/
│   └── tracer.py                # Langfuse callbacks + ConsoleTracer
│
├── eval/
│   ├── agent_eval.py            # routing accuracy + e2e tests
│   └── rag_eval.py              # RAGAS metrics
│
├── ui/
│   └── app.py                   # Gradio 6.x — 4-tab dashboard (Soft theme)
│
├── chase/                       # Telegram notifier + PostgreSQL (auto-starts with app)
│   ├── telegram_bot.py          # polling bot; auto-started by main.py
│   ├── notifier.py              # notifier daemon; auto-started by main.py
│   ├── db.py                    # psycopg2 CRUD; connect_timeout=3
│   └── schema.sql               # PostgreSQL DDL
│
├── scripts/                     # utilities
│   ├── generate_synthetic.py
│   ├── test_pipeline.py
│   └── export_results.py
│
├── test_docs/                   # 8 synthetic documents + manifest.json
│
└── data/
    ├── chroma_db/               # vector store + SQLite conversation checkpoints
    └── uploads/                 # sandboxed workspace for code execution
```

---

## Troubleshooting

**`(.venv)` is not in my prompt** — The virtual environment is not active.
Run `source .venv/bin/activate` from the `dave/` folder. You must do this every
time you open a new terminal.

**`python: command not found`** — Try `python3.11` instead. If neither works,
the venv is not active.

**`ModuleNotFoundError`** — Wrong Python version or venv not active. Check:
```bash
python --version  # must be 3.11.x
```

**`ANTHROPIC_API_KEY not set`** — Open `.env` and make sure the line reads
`ANTHROPIC_API_KEY=sk-ant-...` with your actual key (not the placeholder text).

**Audit returns 0 documents** — The demo corpus hasn't been generated yet:
```bash
python -m domain.demo_corpus.generate_sample
```

**First startup takes a long time** — Normal. On first run, `sentence-transformers`
downloads the `all-MiniLM-L6-v2` embedding model (~90 MB). This only happens once.

**`sqlite3.OperationalError: database is locked`** — Another `main.py` process is
still running. Kill it:
```bash
pkill -f main.py
```

**Critic returns a rate-limit error (Groq 413/429)** — The conversation is too long
for Groq's free tier. Start a new session by clicking **🔄 New session**, or increase
your Groq plan. This does not affect the auditor (which uses Anthropic).

**Analytics tab shows "not connected"** — Expected when `DB_ENABLED=false` (the
default). Set `DB_ENABLED=true` and configure `DB_HOST`/`DB_NAME`/`DB_USER`/
`DB_PASSWORD` in `.env` to activate it. The tab makes zero DB calls when disabled.

**Scanned PDF passes clean / shows 0 findings** — If a PDF is image-only (no text
layer), DAVE flags it with "No text extracted — manual review required" and forces
its trust score below 0.70. If it still passes, the PDF does have a text layer —
use `python -m domain.inspect_doc file.pdf` to confirm what text was extracted.

**Semantic button returns no results** — Either `SEMANTIC_ENABLED=false` (default),
ChromaDB is empty, or no scan has been run yet. Steps:
1. Run a Scan Folder first (populates `docs_state`)
2. Set `SEMANTIC_ENABLED=true` in `.env` and restart
3. Load the standards: `python -m scripts.load_standards`

**Chat seems to return without the Reflexion trace** — The critic is off by default
(`CRITIC_ENABLED=false`). Set `CRITIC_ENABLED=true` to re-enable the scoring loop.

---

## Key design decisions

**Why LangGraph instead of a simple chain?**
Chains are DAGs — they can't loop. DAVE needs cycles: the Reflexion loop sends
responses back to the specialist for revision, and HITL pauses mid-graph and resumes
from a checkpoint. LangGraph's `StateGraph` makes these patterns first-class citizens.

**Why does the audit pipeline use no LLM?**
Running an LLM over 42 files would make ~170 API calls, cost several dollars, and
take minutes. The domain pipeline is pure Python — it runs in under a second on the
same corpus. The LLM's job is to *interpret* the pipeline's output, not to do the
scanning itself.

**Why Anthropic for agents and Groq for the critic?**
Claude handles structured-output routing and multi-step tool use reliably.
The critic only needs to produce a JSON with one float — Groq's Llama-3.3-70B does
this in ~0.8 seconds for near-zero cost. `ROLE_PROVIDER_<ROLE>` makes this
switchable without touching code.

**Why `temperature=0.0`?**
`SupervisorDecision` and `CriticDecision` are Pydantic models parsed from JSON.
Determinism at temperature 0 makes these reliable. Set `TEMPERATURE=0.3` in `.env`
if you want more creative prose from the General agent.

**Why SQLite for the checkpoint?**
Zero infrastructure. The conversation state is a single file in `data/chroma_db/`.
For production, swap to `langgraph-checkpoint-postgres` by changing one line in
`graph/workflow.py`.

---

## Roadmap

- [x] Docker image (`Dockerfile` + `.dockerignore`)
- [x] Telegram notifier daemon (auto-starts with app; gated on `DB_ENABLED`)
- [x] Single-document inspection CLI (`domain/inspect_doc.py`)
- [x] Analytics tab with Postgres backend (opt-in via `DB_ENABLED`)
- [x] Scanned PDF / empty-text detection
- [x] Per-file extraction timeout (prevents hangs on corrupt files)
- [x] Semantic compliance layer (`domain/semantic.py`, `SEMANTIC_ENABLED`)
- [x] Metadata consistency check (author, never-revised, body-date skew)
- [x] Critic optional + off by default (`CRITIC_ENABLED=false`)
- [x] Per-role LLM output token caps (32× cost reduction vs 64k default)
- [x] UI freeze fix: `gr.Plot + None` root cause eliminated
- [x] Owner routing: findings routed to real owners via document author metadata
- [ ] Docker Compose: one-command start with Postgres + app
- [ ] PDF report generation from audit results
- [ ] LangGraph Studio visual debugger integration
- [ ] Browser agent via Playwright MCP server
- [ ] Streaming token-by-token output from specialist agents

---

## License

MIT
