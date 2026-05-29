"""
Gradio UI — full SOTA version.

New features over v1:
  - Agent trace panel: supervisor routing + critic scores
  - HITL approval panel: appears when graph is interrupted
  - Guardrail warning display
  - Mem0 memory sidebar (show/clear episodic memories)
  - Model + HITL toggle controls
"""
from __future__ import annotations

import threading
import uuid
from typing import Iterator

import gradio as gr
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from loguru import logger

from config import settings
from graph.workflow import build_graph, run_query
from graph.workflow import resume_after_hitl
from memory import ingest_documents, get_all_memories
from guardrails import input_guard


# ── Graph singleton ───────────────────────────────────────────────────────────

_mcp_tools = []  # MCP disabled (async-only, incompatible with sync graph invocation)

_graph = build_graph(mcp_tools=_mcp_tools)
_graph_lock = threading.Lock()

# Track per-thread HITL state: thread_id → interrupted payload
_hitl_pending: dict[str, dict] = {}


# ── Chat logic ────────────────────────────────────────────────────────────────

def chat(
    message: str,
    history: list,
    thread_id: str,
    model: str,
    enable_hitl: bool,
) -> Iterator[tuple[list, str, gr.update, gr.update, gr.update]]:
    """
    Yields: (history, trace_md, hitl_row_visible, hitl_response_text, warnings_text)
    """
    if not message.strip():
        yield history, "", gr.update(visible=False), gr.update(value=""), gr.update(value="")
        return

    settings.ollama_model = model

    # Input guard
    guard = input_guard(message)
    warnings_text = ""
    if not guard.passed:
        history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": f"⚠️ {guard.reason}"}]
        yield history, "", gr.update(visible=False), gr.update(value=""), gr.update(value=f"🚫 {guard.reason}")
        return
    if guard.warnings:
        warnings_text = "⚠️ " + " | ".join(guard.warnings)

    history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": ""}]
    trace_lines: list[str] = []

    try:
        from observability import get_callbacks
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": get_callbacks(),
        }
        initial_state = {
            "messages": [HumanMessage(content=guard.sanitised_text)],
            "next_agent": "",
            "reasoning": "",
            "last_specialist": "",
            "supervisor_rounds": 0,
            "critique": "",
            "critique_score": 0.0,
            "should_revise": False,
            "revision_count": 0,
            "retrieved_context": "",
            "hitl_required": enable_hitl,
        }

        with _graph_lock:
            stream = _graph.stream(initial_state, config=config, stream_mode="values")
            accumulated = ""

            for event in stream:
                msgs = event.get("messages", [])
                reasoning = event.get("reasoning", "")
                next_agent = event.get("next_agent", "")
                score = event.get("critique_score")
                critique = event.get("critique", "")
                should_revise = event.get("should_revise", False)

                # Trace: supervisor routing
                if reasoning and next_agent and next_agent not in ("", "FINISH"):
                    trace_lines.append(
                        f"🔀 **Supervisor** → `{next_agent}`\n   _{reasoning}_"
                    )

                # Trace: critic decision
                if score is not None and score > 0:
                    icon = "🔁" if should_revise else "✅"
                    trace_lines.append(
                        f"{icon} **Critic** score=`{score:.0%}`"
                        + (f"\n   _{critique[:80]}_" if critique and should_revise else "")
                    )

                # Stream AI response tokens
                for msg in msgs:
                    if isinstance(msg, AIMessage) and msg.content:
                        accumulated = msg.content
                        history[-1]["content"] = accumulated
                    elif isinstance(msg, ToolMessage):
                        name = getattr(msg, "name", "tool")
                        trace_lines.append(f"🔧 **{name}**")

                yield (
                    history,
                    "\n\n".join(dict.fromkeys(trace_lines)),
                    gr.update(visible=False),
                    gr.update(value=""),
                    gr.update(value=warnings_text),
                )

        # Check if graph was interrupted (HITL)
        state = _graph.get_state(config)
        if state.next and "hitl" in str(state.next):
            interrupt_val = state.tasks[0].interrupts[0].value if state.tasks else {}
            _hitl_pending[thread_id] = {**interrupt_val, "config": config}
            response_preview = interrupt_val.get("response", "")[:500]
            score_str = f"\n\n**Critic score**: {interrupt_val.get('critique_score', 'N/A')}"

            history[-1]["content"] = accumulated or "(awaiting approval)"
            yield (
                history,
                "\n\n".join(trace_lines) + "\n\n⏸️ **Paused — awaiting human approval**",
                gr.update(visible=True),
                gr.update(value=response_preview + score_str),
                gr.update(value=warnings_text),
            )
            return

        history[-1]["content"] = accumulated or "(no response)"
        yield (
            history,
            "\n\n".join(trace_lines) + "\n\n✅ **Done**",
            gr.update(visible=False),
            gr.update(value=""),
            gr.update(value=warnings_text),
        )

    except Exception as e:
        logger.error(f"Chat error: {e}")
        history[-1]["content"] = f"❌ Error: {e}"
        yield history, f"Error: {e}", gr.update(visible=False), gr.update(value=""), gr.update(value="")


def handle_hitl(thread_id: str, approved: bool, feedback: str) -> tuple[list, str]:
    """Called when user approves or rejects in the HITL panel."""
    pending = _hitl_pending.pop(thread_id, None)
    if not pending:
        return [], "No pending HITL request."

    config = pending["config"]
    try:
        result = resume_after_hitl(_graph, thread_id, approved=approved, feedback=feedback)
        trace = "✅ Approved and sent." if approved else f"🔁 Sent back for revision: {feedback}"
        return [{"role": "assistant", "content": result}], trace
    except Exception as e:
        logger.error(f"HITL resume error: {e}")
        return [], f"Error resuming: {e}"


def upload_docs(files, progress=gr.Progress()) -> str:
    if not files:
        return "No files selected."
    paths = [f.name for f in files]
    progress(0, desc="Ingesting…")
    try:
        n = ingest_documents(paths, source_tag="user_upload")
        return f"✅ {n} chunks ingested from {len(paths)} file(s)."
    except Exception as e:
        return f"❌ {e}"


def show_memories(thread_id: str) -> str:
    facts = get_all_memories(user_id=thread_id)
    if not facts:
        return "_No episodic memories yet._"
    return "\n".join(f"- {f}" for f in facts)


def new_session() -> str:
    return str(uuid.uuid4())[:8]


# ── UI ────────────────────────────────────────────────────────────────────────

OLLAMA_MODELS = [
    "llama3.2", "llama3.1", "qwen2.5", "qwen2.5:14b",
    "mistral", "gemma3", "phi4", "deepseek-r1",
]

EXAMPLES = [
    "What is LangGraph and how does the supervisor pattern work?",
    "Write Python to compute and plot a confusion matrix.",
    "Search for recent papers on agentic AI.",
    "Explain SHAP values with a code example using XGBoost.",
    "Remember that I prefer pandas for data manipulation.",
    "What do you remember about my preferences?",
]


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Agentic AI") as demo:

        gr.Markdown(
            "# 🤖 Agentic AI\n"
            "**LangGraph · Ollama · RAG · Reflexion · HITL · MCP · Mem0**",
        )

        with gr.Row():
            # ── Left: Chat ────────────────────────────────────────────────────
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(height=500)

                # HITL approval panel (hidden by default)
                with gr.Group(visible=False) as hitl_row:
                    gr.Markdown("### ⏸️ Human Review Required")
                    hitl_response = gr.Textbox(
                        label="Agent's proposed response",
                        lines=5,
                        interactive=False,
                    )
                    hitl_feedback = gr.Textbox(
                        label="Feedback (if rejecting)",
                        placeholder="What should be improved?",
                    )
                    with gr.Row():
                        approve_btn = gr.Button("✅ Approve", variant="primary")
                        reject_btn  = gr.Button("🔁 Reject & revise", variant="secondary")

                warnings_box = gr.Markdown(value="", visible=True)

                with gr.Row():
                    msg_box  = gr.Textbox(placeholder="Ask anything…", label="", lines=2, scale=5)
                    send_btn = gr.Button("Send ▶", variant="primary", scale=1)

                gr.Examples(examples=EXAMPLES, inputs=msg_box, label="Examples")

            # ── Right: Controls + Trace + Memory ─────────────────────────────
            with gr.Column(scale=1):
                model_dd = gr.Dropdown(
                    choices=OLLAMA_MODELS, value=settings.ollama_model,
                    label="Ollama model", interactive=True,
                )
                hitl_toggle = gr.Checkbox(
                    label="🔒 Enable human-in-the-loop review", value=False
                )

                thread_state   = gr.State(new_session())
                thread_display = gr.Textbox(label="Session ID", interactive=False, value=new_session())
                new_session_btn = gr.Button("🔄 New session")

                gr.Markdown("---\n### 🔍 Agent trace")
                trace_box = gr.Markdown(value="_(trace will appear here)_")

                gr.Markdown("---\n### 🧠 Episodic memory")
                recall_btn  = gr.Button("Show memories")
                memory_box  = gr.Markdown(value="")

                gr.Markdown("---\n### 📄 Add to RAG memory")
                file_upload  = gr.File(
                    label=".txt / .md / .pdf",
                    file_types=[".txt", ".md", ".pdf"],
                    file_count="multiple",
                )
                upload_btn    = gr.Button("📥 Ingest")
                upload_status = gr.Textbox(label="Status", interactive=False)

        # ── Wiring ────────────────────────────────────────────────────────────

        def submit(msg, hist, tid, model, hitl):
            yield from chat(msg, hist, tid, model, hitl)

        send_btn.click(
            submit,
            inputs=[msg_box, chatbot, thread_state, model_dd, hitl_toggle],
            outputs=[chatbot, trace_box, hitl_row, hitl_response, warnings_box],
        ).then(lambda: "", outputs=msg_box)

        msg_box.submit(
            submit,
            inputs=[msg_box, chatbot, thread_state, model_dd, hitl_toggle],
            outputs=[chatbot, trace_box, hitl_row, hitl_response, warnings_box],
        ).then(lambda: "", outputs=msg_box)

        approve_btn.click(
            lambda tid, fb: handle_hitl(tid, approved=True,  feedback=fb),
            inputs=[thread_state, hitl_feedback],
            outputs=[chatbot, trace_box],
        ).then(lambda: gr.update(visible=False), outputs=hitl_row)

        reject_btn.click(
            lambda tid, fb: handle_hitl(tid, approved=False, feedback=fb),
            inputs=[thread_state, hitl_feedback],
            outputs=[chatbot, trace_box],
        ).then(lambda: gr.update(visible=False), outputs=hitl_row)

        def reset():
            sid = new_session()
            return sid, sid, [], "_(new session)_", ""

        new_session_btn.click(
            reset, outputs=[thread_state, thread_display, chatbot, trace_box, memory_box]
        )

        recall_btn.click(show_memories, inputs=thread_state, outputs=memory_box)
        upload_btn.click(upload_docs, inputs=file_upload, outputs=upload_status)

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860)
