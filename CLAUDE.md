# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate venv (required in every session)
source .venv/bin/activate

# Run the app
python main.py                     # Gradio UI at http://localhost:7860
python main.py --cli               # Interactive terminal chat
python main.py --query "..."       # Single-shot query

# Evals
python -m eval.agent_eval          # All evals (routing + e2e)
python -m eval.agent_eval --routing
python -m eval.agent_eval --e2e
python -m eval.rag_eval

# Install / update dependencies
pip install -r requirements.txt
```

> Python 3.11 is required. Python 3.12+ breaks Mem0 and ChromaDB.

## Configuration

Copy `.env.example` to `.env`. The most critical settings:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | Default provider: `anthropic`, `groq`, `ollama` |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=anthropic` |
| `GROQ_API_KEY` | — | Required when `LLM_PROVIDER=groq` |
| `OLLAMA_MODEL` | `llama3.2` | Model name when using Ollama |
| `MAX_SUPERVISOR_ROUNDS` | `5` | Hard cap on routing loops |
| `ROLE_PROVIDER_<ROLE>` | — | Per-role provider override (e.g. `ROLE_PROVIDER_CRITIC=groq`) |
| `LANGFUSE_PUBLIC_KEY` | — | Optional: enables cloud tracing |

## Architecture

### Graph topology (`graph/workflow.py`)

```
START → guardrail_in → supervisor
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          researcher     coder       general
              └────────────┼────────────┘
                           ▼
                         critic  ← Reflexion scoring (CriticDecision)
                        ↙      ↘
              (revise back     (score ≥ 0.70)
               to specialist)       │
                                    ▼
                                   hitl → END
```

- `guardrail_in`: prompt injection detection + PII redaction before supervisor sees the input
- `supervisor`: structured-output routing via `SupervisorDecision` (Pydantic); hard-stops at `MAX_SUPERVISOR_ROUNDS`
- Three specialist agents: all are `create_react_agent` (ReAct) with tool sets + memory tools
- `critic`: scores the last AI message with `CriticDecision`; routes back to the same specialist if `score < 0.70`, up to 2 revisions
- `hitl`: uses LangGraph `interrupt()` to pause for human approval; resumes via `resume_after_hitl()`
- State: single `AgentState` TypedDict flows through all nodes (`graph/state.py`)
- Persistence: SQLite checkpointer at `data/chroma_db/checkpoints.sqlite` — conversations resume by `thread_id`

### LLM factory (`agents/llm.py`)

`get_llm(role=...)` is `@lru_cache`-decorated. Provider selection order:
1. `ROLE_PROVIDER_<ROLE>` env var
2. `LLM_PROVIDER` setting

Embeddings (RAG + Mem0) always use Ollama `nomic-embed-text` regardless of `LLM_PROVIDER`.

### Memory

- **Semantic / RAG** (`memory/vector_store.py`): ChromaDB + `sentence-transformers/all-MiniLM-L6-v2` (CPU, no API key). Documents ingested from the UI are chunked and persisted in `data/chroma_db/`.
- **Episodic** (`memory/episodic.py`): Mem0 backed by Ollama. `remember` and `recall` are LangChain tools injected into every agent.

### Observability (`observability/tracer.py`)

`get_callbacks()` returns Langfuse callbacks if `LANGFUSE_PUBLIC_KEY` is set, otherwise a local `ConsoleTracer`. Always passed via the `config` dict to every graph invocation.

### Evals (`eval/`)

- `eval/agent_eval.py`: routing accuracy (supervisor decision) and keyword-match e2e tests
- `eval/rag_eval.py`: RAGAS metrics (answer correctness, faithfulness, context recall)
- Results written to `eval/results/`

### MCP servers (`mcp_servers/`)

Experimental. `loader.py` loads filesystem MCP tools via `langchain-mcp-adapters`. Injected into all agents via `build_graph(mcp_tools=[...])`.
