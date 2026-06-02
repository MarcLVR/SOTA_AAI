# 🤖 SOTA Agentic AI

A **state-of-the-art multi-agent system** with pluggable LLM providers.
Showcases the complete modern agentic stack:
LangGraph · Reflexion · HITL · RAG · Episodic Memory · Guardrails · Langfuse.

Supports **Gemini, Groq, and Ollama** (local). No single vendor lock-in.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Gradio UI                              │
│       streaming chat · trace panel · RAG upload · HITL panel   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  guardrail_in   │  prompt injection · PII redaction
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Supervisor    │  structured routing · loop guard
                    └──┬──────┬───┬──┘
                       │      │   │
             ┌─────────▼─┐ ┌──▼──┐ ┌▼───────────────┐
             │ Researcher│ │Coder│ │    General     │
             │  ReAct    │ │ReAct│ │    ReAct       │
             └─────┬─────┘ └──┬──┘ └───────┬────────┘
                   └──────────┴────────────┘
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
Memory:      ChromaDB (semantic) · Mem0 (episodic) · SQLite (conversation state)
Observability: Langfuse traces · ConsoleTracer · RAGAS evals
```

---

## Features

| Feature | Technology |
|---|---|
| Multi-agent orchestration | LangGraph (supervisor pattern) |
| Specialist agents | ReAct (`create_react_agent`) — Researcher, Coder, General |
| **Reflexion / self-critique** | Critic node with structured `CriticDecision` scoring (0–1) |
| **Human-in-the-loop (HITL)** | LangGraph `interrupt()` — pause, review, approve or reject |
| **Episodic memory** | Mem0 (Ollama-backed) — remembers facts across sessions |
| **Input/output guardrails** | Prompt injection detection · PII redaction |
| **Observability** | Langfuse cloud tracing + local ConsoleTracer |
| **Eval harness** | RAGAS (answer correctness, faithfulness) + agent evals |
| RAG / semantic memory | ChromaDB + `sentence-transformers` (CPU, no API key) |
| Sandboxed code execution | subprocess + timeout + matplotlib support |
| Tool use | DuckDuckGo web search, Python REPL, File I/O |
| MCP servers | `langchain-mcp-adapters` → filesystem (experimental) |
| Conversation persistence | SQLite checkpointer (resume by thread ID) |
| LLM providers | **Gemini** (default) · **Groq** (fast, free tier) · **Ollama** (local) |
| Per-role overrides | `ROLE_PROVIDER_<ROLE>` — pin any agent to any provider |
| UI | Gradio 6.x (streaming, file upload, agent trace panel) |
| Config | `pydantic-settings` + `.env` · per-role provider overrides |

---

## Installation

### Option A — Windows with WSL2 ✅ Tested

This is the recommended path on Windows. The app runs inside Ubuntu on WSL2,
while Ollama runs natively on Windows for best GPU/CPU performance.

#### Step 1 — Install WSL2 + Ubuntu

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Ubuntu
```

Restart your computer when prompted. After restart, Ubuntu will open automatically
and ask you to create a Linux username and password (choose anything you like).

To open Ubuntu in the future: search **Ubuntu** in the Start Menu, or type `wsl` in any terminal.

#### Step 2 — (Optional) Install Ollama on Windows

Only needed if you want `LLM_PROVIDER=ollama`. Skip if using Gemini or Groq.

Download and install Ollama from [ollama.ai/download](https://ollama.ai/download).
Ollama runs automatically as a Windows background service.

Open a **Windows** terminal (PowerShell or CMD — not Ubuntu) and pull the models:

```powershell
ollama pull llama3.2           # chat model
ollama pull nomic-embed-text   # required for RAG + episodic memory embeddings
```

> Ollama is accessible from WSL2 at `http://localhost:11434` automatically.

#### Step 3 — Install Python 3.11 in Ubuntu

Open Ubuntu and run:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip git
```

Verify:

```bash
python3.11 --version   # should print Python 3.11.x
```

> ⚠️ Use Python 3.11 specifically. Python 3.12+ breaks some ML dependencies (Mem0, ChromaDB).

#### Step 4 — Clone the repo

Inside Ubuntu:

```bash
git clone https://github.com/MarcLVR/SOTA_AAI.git
cd SOTA_AAI
```

#### Step 5 — Create virtual environment and install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

First-time installation takes 5–10 minutes (downloads ML models and dependencies).

#### Step 6 — Configure

```bash
cp .env.example .env
nano .env
```

Choose one LLM provider and set its key:

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

Optional — pin specific agents to a different provider:

```env
ROLE_PROVIDER_CRITIC=groq      # fast critic scoring
ROLE_PROVIDER_SUPERVISOR=gemini
```

Optional — Langfuse observability:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

Save with `Ctrl+X`, then `Y`, then Enter.

#### Step 7 — Run

```bash
source .venv/bin/activate   # required every new terminal session
python main.py
```

Open your **Windows browser** at **[http://localhost:7860](http://localhost:7860)**.

---

### Option B — Linux / macOS

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve &

# Pull models
ollama pull llama3.2
ollama pull nomic-embed-text

# Clone and install
git clone https://github.com/MarcLVR/SOTA_AAI.git
cd SOTA_AAI
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure and run
cp .env.example .env
# edit .env with your preferred editor
python main.py
```

---

## Using the UI

Open [http://localhost:7860](http://localhost:7860) in your browser. You will see:

```
┌─────────────────────────────┬──────────────────────────────┐
│         Chat panel          │        Trace panel           │
│                             │                              │
│  Type your message here     │  Shows agent routing,        │
│  and receive a streamed     │  critic scores, and tool     │
│  response                   │  calls in real time          │
│                             │                              │
└─────────────────────────────┴──────────────────────────────┘
│              Add to memory (RAG upload panel)               │
│   Upload .txt / .md / .pdf to give the agent context        │
└─────────────────────────────────────────────────────────────┘
```

### Chat panel

Type any question or task and press Enter. The agent streams its response in real time.
You do not need to specify which agent to use — the supervisor routes automatically
based on your question.

**Session ID** — shown at the top. The same ID resumes the same conversation
(including episodic memory) across restarts. Change it to start a fresh session.

**Model selector** — switch between any Ollama model you have pulled without restarting.

**Enable HITL** — toggle to turn on Human-in-the-Loop review mode (see below).

### Trace panel

Every query shows a live trace of what the system is doing internally:

```
🔀 Supervisor → researcher   The user is asking about recent papers — web search needed
🔧 web_search                searching DuckDuckGo...
🔁 Critic score=65%          Response lacks citations — requesting revision
🔀 Supervisor → researcher   (revision pass with critique injected)
✅ Critic score=91%          Response is clear and well-sourced
✅ Done
```

Icons:
- `🔀` — supervisor routing decision and reasoning
- `🔧` — tool call (web search, code execution, file read, etc.)
- `🔁` — critic requested a revision (score below 0.70 threshold)
- `✅` — critic approved the response

### Add to memory (RAG)

Upload documents that the Researcher agent can search when answering questions.

1. Click **Upload files** and select one or more `.txt`, `.md`, or `.pdf` files
2. Click **Ingest** — files are chunked and stored in ChromaDB locally
3. Ask a question related to the documents — the agent retrieves and cites them automatically

Example: upload a research paper, then ask *"Summarise the methodology in the paper I uploaded"*.

---

## Agent guide — what to ask each agent

The supervisor routes automatically, but understanding each agent helps you write better prompts.

### 🔍 Researcher

Best for: finding information, summarising topics, literature reviews, fact-checking, news.

Uses web search (DuckDuckGo) and your uploaded documents (RAG).

```
"What are the most cited papers on Retrieval-Augmented Generation from 2024?"
"Find recent news about LLaMA model releases"
"Summarise what I need to know about the EU AI Act"
"Search for benchmarks comparing LLaMA vs Mistral vs Qwen"
"What did the paper I uploaded say about model evaluation?"
```

### 💻 Coder

Best for: writing code, debugging, running calculations, data analysis, generating plots.

Executes Python in an isolated sandbox with a timeout. Can produce matplotlib plots
saved to `data/uploads/`.

```
"Write a Python function to compute a confusion matrix and plot it with seaborn"
"Debug this code: [paste your code]"
"Calculate the first 20 Fibonacci numbers"
"Write a script to parse a CSV and compute summary statistics"
"Implement a logistic regression with cross-validation using scikit-learn"
```

> Code runs locally in a sandbox — it cannot access the internet or your filesystem
> outside `data/uploads/`.

### 🧠 General

Best for: reasoning, explanation, writing, analysis, brainstorming, multi-step thinking.

Uses episodic memory — can remember facts you tell it and recall them later.

```
"Explain the difference between RAG and fine-tuning"
"My name is Marc and I work in credit risk — remember this"
"What do you know about me?"   ← retrieves from episodic memory
"Write a technical explanation of LangGraph for a senior engineer"
"Compare XGBoost and LightGBM for credit scoring"
"Help me design the architecture for a document Q&A system"
```

---

## Reflexion — how the self-critique loop works

After every specialist responds, the **Critic** node evaluates the response:

1. Scores it from 0.0 to 1.0 using structured output (`CriticDecision`)
2. If score < 0.70 → injects the critique as a hint and sends back for revision
3. If score ≥ 0.70 → accepts the response and exits to END
4. Maximum 2 revision rounds — prevents infinite loops

You can watch this in the trace panel. A `🔁` means a revision was requested with the
critique shown. A `✅` means it passed.

> **Note:** `llama3.2` (3B) can be inconsistent at self-scoring. For more reliable
> Reflexion use `llama3.1:8b`: set `OLLAMA_MODEL=llama3.1:8b` in `.env`.

---

## Human-in-the-Loop (HITL)

HITL lets you review and approve the agent's response before it is returned.
Useful for high-stakes tasks where you want a final check.

**To enable:** tick **"Enable HITL"** in the UI before sending your message.

**What happens:**

1. The agent generates a response and the critic scores it
2. Execution pauses and an **approval panel** appears below the chat
3. You see the proposed response and the critic score
4. Click **Approve** to return the response as-is
5. Or type feedback and click **Reject** — the agent revises and pauses again for re-approval

Example feedback on rejection: *"Make it shorter"*, *"Add a code example"*, *"Cite your sources"*.

---

## Episodic Memory

Agents remember facts across sessions. There is nothing to configure — it works automatically.

**Storing a fact** (just tell the agent):

```
"Remember that I prefer concise answers with code examples"
"My team uses LightGBM for all production credit models — remember this"
"I am based in Spain and work under EU regulations"
```

**Recalling facts:**

```
"What do you remember about my preferences?"
"What do you know about my tech stack?"
"Do you remember where I'm based?"
```

Facts are stored in Mem0 (backed by Ollama locally) and persist in `data/chroma_db/mem0`
across restarts and different sessions.

---

## Observability

### Langfuse (cloud, free tier)

1. Create a free account at [cloud.langfuse.com](https://cloud.langfuse.com)
2. Create a project → copy the public key, secret key, and host URL
3. Add them to `.env`:
   ```env
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://cloud.langfuse.com
   ```
4. Restart the app — every query now produces a trace in your Langfuse dashboard

Each trace shows the full span tree: supervisor decision → specialist LLM call →
tool calls → critic LLM call → latencies and token counts.

### Local console tracing

Without Langfuse, all events are logged to the terminal automatically:

```
[LLM] ▶ ? | 1 prompt(s)
[tool] ▶ web_search({'query': 'agentic AI papers'})
[tool] ✓ content='[1] How Agentic AI...'
[LLM] ✓ 3.25s | tokens=?
[critic] score=0.92 revise=False
```

---

## Evals

```bash
source .venv/bin/activate
python evals/run_evals.py
```

Runs RAGAS metrics (answer correctness, faithfulness, context recall) over a built-in
test suite and prints a summary table. Add your own test cases in `evals/test_cases.py`.

---

## Project structure

```
SOTA_AAI/
├── main.py                  # entrypoint (UI / CLI / single query)
├── requirements.txt
├── .env.example             # template — copy to .env and fill in
├── config/
│   └── settings.py          # pydantic-settings config singleton
├── agents/
│   ├── llm.py               # LLM factory — Gemini / Groq / Ollama, per-role override
│   ├── supervisor.py        # structured routing node
│   ├── researcher.py        # web search + RAG ReAct agent
│   ├── coder.py             # Python REPL ReAct agent
│   ├── general.py           # catch-all reasoning agent
│   └── critic.py            # Reflexion critic (CriticDecision scoring)
├── graph/
│   ├── state.py             # AgentState TypedDict
│   └── workflow.py          # LangGraph builder — full SOTA topology
├── tools/
│   ├── web_search.py        # DuckDuckGo search
│   ├── code_executor.py     # sandboxed subprocess Python REPL
│   └── file_tools.py        # read / write / list files
├── memory/
│   ├── vector_store.py      # ChromaDB + HF embeddings (semantic RAG)
│   └── episodic.py          # Mem0 episodic memory (Ollama-backed)
├── guardrails/
│   ├── input_guard.py       # prompt injection detection · PII redaction
│   └── output_guard.py      # output safety checks
├── observability/
│   └── tracer.py            # Langfuse callback + ConsoleTracer
├── evals/
│   ├── run_evals.py         # RAGAS + agent eval runner
│   └── test_cases.py        # eval dataset
├── mcp_servers/
│   ├── config.py            # MCP server registry
│   └── loader.py            # MCP tool loader (experimental)
├── ui/
│   └── app.py               # Gradio 6.x streaming UI
└── data/
    ├── chroma_db/           # persisted vector stores
    └── uploads/             # sandboxed file workspace
```

---

## Troubleshooting

**`Command 'python' not found`**

Activate the virtual environment first:
```bash
source .venv/bin/activate
```
You must run this every time you open a new terminal.

**Gemini 429 rate limit**

Free tier is 15–20 requests/minute. Switch high-volume agents to Groq:
```env
ROLE_PROVIDER_CRITIC=groq
```

**`Connection refused` / Ollama not reachable**

On Windows: check the system tray for the Ollama icon. If it's missing, launch Ollama
from the Start Menu. On Linux/macOS: run `ollama serve` in a separate terminal.
Verify with: `curl http://localhost:11434/api/tags`

**`Authentication error: Langfuse client initialized without public_key`**

Your `.env` keys aren't loading. Verify the file exists and contains the keys
(not `.env.example`). The app loads `.env` automatically on startup via `python-dotenv`.

**Model outputs are poor / supervisor loops excessively**

`llama3.2` (3B) has limited instruction-following for complex routing tasks.
Switch to a larger model:
```bash
ollama pull llama3.1:8b
# In .env: OLLAMA_MODEL=llama3.1:8b
```

**Gradio UI not loading**

Check the terminal — the app prints the URL when ready.
If using WSL2, open `http://localhost:7860` in your **Windows browser**, not inside WSL2.

**Out of memory / very slow responses**

`llama3.2` uses ~2GB RAM. `llama3.1:8b` uses ~5GB.
Close other applications. The embedding model (`nomic-embed-text`) loads separately
and uses an additional ~300MB.

**First startup is slow**

On first run, `sentence-transformers` downloads the embedding model (~90MB).
This only happens once — subsequent starts are fast.

---

## Key design decisions

**Why LangGraph over pure LCEL?**
Explicit state, conditional routing, and cycle support — essential for Reflexion loops
and HITL interrupts that need to pause and resume mid-graph.

**Why Reflexion instead of single-shot?**
Self-critique measurably improves output quality on open-ended tasks. The critic uses
structured output (`CriticDecision`) to score and explain revisions, capped at 2 rounds
to prevent infinite loops.

**Why local embeddings?**
`sentence-transformers/all-MiniLM-L6-v2` runs on CPU in ~50ms with no API cost
and no data leaving the machine.

**Why SQLite checkpointer?**
Zero-dependency persistence. Swap to `langgraph-checkpoint-postgres` for production.

**Why role-based provider routing?**
Supervisor and agents need strong instruction-following (Gemini); critic needs
sub-second latency (Groq). `ROLE_PROVIDER_<ROLE>` lets each role use its best
model without changing shared config.

**Why Mem0 for episodic memory?**
Mem0 abstracts memory CRUD over any LLM+vector backend. Configured here to use
Ollama embeddings locally — swap the backend via env vars for production.

---

## Roadmap

- [ ] Browser agent via Playwright MCP server
- [ ] Docker Compose setup (Ollama + app in one command)
- [ ] Streaming token-by-token output from specialist agents
- [ ] Multi-turn HITL (back-and-forth revision loop)
- [ ] LangGraph Studio visual debugger integration

---

## License

MIT
