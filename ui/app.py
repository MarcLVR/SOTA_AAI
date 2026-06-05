"""
Gradio UI — 3-tab version.

Tab 1 — Scan        : audit_repository() dashboard (no LLM, quota-safe)
Tab 2 — Documents   : per-document dataframe + drill-in detail panel
Tab 3 — Ask agent   : existing supervisor/multi-agent chat interface
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from typing import Iterator

import gradio as gr
import plotly.graph_objects as go
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from loguru import logger

from config import settings
from domain.adapter import audit_and_persist
from domain.semantic import semantic_check
from guardrails.permissions import check_action, resolve_role
from guardrails.elicitation import get_pending, submit_response, decline as decline_elicitation
from domain.tools.aggregate import human_bytes
from graph.workflow import build_graph, run_query, resume_after_hitl
from memory import ingest_documents, get_all_memories
from guardrails import input_guard


# ── chase/db lazy import helper ──────────────────────────────────────────────

def _chase_db():
    """Lazy-import analytics functions from chase/db.py (not a package)."""
    import sys, os
    _chase = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chase")
    if _chase not in sys.path:
        sys.path.insert(0, _chase)
    from db import (
        get_dashboard_stats, get_findings_by_severity, get_findings_by_status,
        get_top_rule_codes, get_findings_by_owner, get_runs_over_time,
    )
    return (get_dashboard_stats, get_findings_by_severity, get_findings_by_status,
            get_top_rule_codes, get_findings_by_owner, get_runs_over_time)


# ── Graph singleton (Tab 3 only) ─────────────────────────────────────────────

_mcp_tools: list = []
_graph = build_graph(mcp_tools=_mcp_tools)
_graph_lock = threading.Lock()
_hitl_pending: dict[str, dict] = {}

DEFAULT_FOLDER = "domain/demo_corpus/files"
EXTRAPOLATION_FACTOR = 30_000
LARGE_FOLDER_WARN = 2000   # warn (but don't block) when a folder exceeds this many files


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 / 2 — pure-Python helpers (no LLM)
# ─────────────────────────────────────────────────────────────────────────────

def _trust_badge(score: float) -> str:
    if score < 0.50:
        return "🔴"
    if score < 0.70:
        return "🟡"
    return "🟢"


def _hero_html(result: dict, extrapolate: bool) -> str:
    if not result:
        return ""
    h = result["headline"]
    pct   = round(h["needs_supervision_pct"])
    count = h["needs_supervision_count"]
    total = h["total_documents"]

    note = ""
    if extrapolate:
        count = round(count * EXTRAPOLATION_FACTOR)
        total = round(total * EXTRAPOLATION_FACTOR)
        note = (
            f'<div style="font-size:12px;color:#6b7280;margin-top:6px;">'
            f'extrapolated ×{EXTRAPOLATION_FACTOR:,} — actual corpus data scaled for illustration'
            f'</div>'
        )

    if pct >= 50:
        color, bg = "#dc2626", "#fee2e2"
    elif pct >= 30:
        color, bg = "#d97706", "#fef3c7"
    else:
        color, bg = "#16a34a", "#dcfce7"

    return (
        f'<div style="text-align:center;padding:28px 20px;background:{bg};'
        f'border-radius:14px;margin:8px 0;">'
        f'<div style="font-size:80px;font-weight:900;color:{color};line-height:1.05;">'
        f'{pct}%</div>'
        f'<div style="font-size:19px;color:#374151;margin-top:8px;">'
        f'<strong>{count:,}</strong> of <strong>{total:,}</strong> documents need supervision'
        f'</div>'
        f'{note}'
        f'</div>'
    )


def _stats_html(result: dict, extrapolate: bool) -> str:
    if not result:
        return ""
    h     = result["headline"]
    est_h = result.get("estimated_remediation_hours", 0)

    docs_label  = (
        f"{h['total_documents'] * EXTRAPOLATION_FACTOR:,} ×{EXTRAPOLATION_FACTOR:,}"
        if extrapolate else f"{h['total_documents']:,}"
    )
    hours_label = (
        f"{round(est_h * EXTRAPOLATION_FACTOR):,} h"
        if extrapolate else f"{round(est_h)} h"
    )
    flagged_pct = round(h.get("flagged_size_pct", 0))

    cards = [
        ("📄", docs_label,                                "Total documents"),
        ("💾", h["total_size_human"],                    "Total size"),
        ("⚠️", f"{h.get('flagged_size_human','—')} ({flagged_pct}%)", "At risk"),
        ("🕐", hours_label,                               "Est. remediation"),
    ]
    inner = "".join(
        f'<div style="flex:1;min-width:110px;background:#f9fafb;border:1px solid #e5e7eb;'
        f'padding:14px 10px;border-radius:10px;text-align:center;">'
        f'<div style="font-size:22px;">{icon}</div>'
        f'<div style="font-size:17px;font-weight:700;color:#111827;margin:4px 0;">{val}</div>'
        f'<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">'
        f'{lbl}</div></div>'
        for icon, val, lbl in cards
    )
    cards_html = f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;">{inner}</div>'

    # Unreadable / cloud-only / locked files warning
    unreadable = result.get("unreadable_files", [])
    if unreadable:
        examples = ", ".join(f"`{u['name']}`" for u in unreadable[:3])
        more = f" +{len(unreadable)-3} more" if len(unreadable) > 3 else ""
        cards_html += (
            f'<div style="margin:8px 0;padding:10px 14px;background:#fef3c7;'
            f'border:1px solid #fcd34d;border-radius:8px;font-size:13px;color:#92400e;">'
            f'⚠️ <strong>{len(unreadable)} file(s) could not be read</strong> '
            f'(cloud-only placeholders or locked): {examples}{more}. '
            f'Open them in Windows to sync before auditing.'
            f'</div>'
        )

    # Large-folder warning
    large_warn = result.get("_large_folder_warning")
    if large_warn:
        cards_html += (
            f'<div style="margin:8px 0;padding:10px 14px;background:#dbeafe;'
            f'border:1px solid #93c5fd;border-radius:8px;font-size:13px;color:#1e40af;">'
            f'{large_warn}</div>'
        )

    return cards_html


def _reasons_fig(result: dict):
    if not result:
        return go.Figure().update_layout(title="Run a scan to see results")
    reasons = result.get("reasons", {})
    label_map = {
        "stale":               "Stale",
        "cold":                "Cold (not accessed)",
        "non_standard_format": "Non-standard format",
        "missing_sections":    "Missing sections",
        "retired_standards":   "Retired standards",
        "placeholder_content": "Placeholder content",
        "no_owner":            "No owner",
        "unclassified":        "Unclassified",
        "missing_metadata":    "Missing metadata",
        "extraction_failed":   "Extraction failed",
    }
    items = [(label_map.get(k, k), v) for k, v in reasons.items() if v > 0]
    items.sort(key=lambda x: x[1])  # ascending → largest bar at top

    if not items:
        return go.Figure().update_layout(title="No flagged documents")

    fig = go.Figure(go.Bar(
        x=[i[1] for i in items],
        y=[i[0] for i in items],
        orientation="h",
        marker_color="#ef4444",
        text=[str(i[1]) for i in items],
        textposition="outside",
    ))
    fig.update_layout(
        title="Top reasons for flagging",
        xaxis_title="Documents",
        height=360,
        margin=dict(l=210, r=60, t=50, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(size=12),
    )
    return fig


def _doc_type_fig(result: dict):
    if not result:
        return go.Figure().update_layout(title="Run a scan to see results")
    by_type = result.get("by_doc_type", {})
    if not by_type:
        return go.Figure().update_layout(title="No document-type data")

    types   = list(by_type.keys())
    counts  = [v["count"]   for v in by_type.values()]
    flagged = [v["flagged"] for v in by_type.values()]

    fig = go.Figure(data=[
        go.Bar(name="Total",   x=types, y=counts,  marker_color="#3b82f6"),
        go.Bar(name="Flagged", x=types, y=flagged, marker_color="#ef4444"),
    ])
    fig.update_layout(
        barmode="group",
        title="By document type",
        xaxis_title="Type",
        yaxis_title="Count",
        height=300,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=20, t=60, b=50),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(size=12),
    )
    return fig


def _build_table_rows(docs_sorted: list) -> list[list]:
    rows = []
    for doc in docs_sorted:
        trust = doc.get("trust_score", 1.0)
        badge = _trust_badge(trust)

        all_findings = (
            doc.get("staleness", {}).get("findings", [])
            + doc.get("standards", {}).get("findings", [])
            + doc.get("governance", {}).get("findings", [])
        )
        top_reason = (all_findings[0][:120] if all_findings else "—")
        modified   = (doc.get("modified_at") or "")[:10] or "—"

        rows.append([
            doc.get("name", ""),
            doc.get("doc_type", ""),
            human_bytes(doc.get("size_bytes", 0)),
            modified,
            f"{badge} {trust:.2f}",
            "Yes" if doc.get("needs_supervision") else "No",
            top_reason,
        ])
    return rows


def _doc_detail_md(row_idx: int, docs: list) -> str:
    if not docs or row_idx is None or row_idx >= len(docs):
        return "_Select a row to see full findings._"

    doc   = docs[row_idx]
    trust = doc.get("trust_score", 1.0)
    badge = _trust_badge(trust)
    s, st, g = doc.get("staleness", {}), doc.get("standards", {}), doc.get("governance", {})

    lines = [
        f"## {doc.get('name', 'Unknown')}",
        f"`{doc.get('path', '')}`  ",
        "",
        f"**Type:** {doc.get('doc_type', '—')} &nbsp;|&nbsp; "
        f"**Size:** {human_bytes(doc.get('size_bytes', 0))} &nbsp;|&nbsp; "
        f"**Modified:** {(doc.get('modified_at') or '')[:10] or '—'}  ",
        f"**Trust score:** {badge} **{trust:.3f}** "
        f"{'— needs supervision' if doc.get('needs_supervision') else '— passes threshold'}  ",
        "",
    ]

    for title, obj in [("Staleness", s), ("Standards", st), ("Governance", g)]:
        findings = obj.get("findings", [])
        if findings:
            lines.append(f"### {title} findings")
            lines.extend(f"- {f}" for f in findings)
            lines.append("")

    meta = doc.get("embedded_metadata") or {}
    if meta:
        lines.append("### Embedded metadata")
        lines.extend(f"- **{k}:** {v}" for k, v in meta.items() if v)
        lines.append("")

    lines += [
        "### Scores",
        f"- Staleness: `{s.get('staleness_score', '—')}`  "
        f"(stale={s.get('is_stale', '—')}, cold={s.get('is_cold', '—')}, "
        f"age={s.get('age_days', '—')} days)",
        f"- Standards: `{st.get('standards_score', '—')}`  "
        f"(standard format={st.get('is_standard_format', '—')})",
        f"- Governance: `{g.get('governance_score', '—')}`  "
        f"(has owner={g.get('has_owner', '—')}, classified={g.get('classification_valid', '—')})",
    ]
    return "\n".join(lines)


# ── Semantic compliance helpers ──────────────────────────────────────────────

def _semantic_result_md(doc_name: str, findings: list[dict]) -> str:
    """Format semantic_check() findings as Markdown for the result panel."""
    lines = [f"### 🧠 Semantic compliance — `{doc_name}`", ""]
    for f in findings:
        compliance = f.get("compliance", "unknown").lower()
        severity   = f.get("severity",   "medium").lower()
        icon   = {"compliant": "✅", "partial": "⚠️", "violation": "❌"}.get(compliance, "❓")
        badge  = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "")
        std    = f.get("standard_matched", "?").removesuffix(".md")
        detail = f.get("detail", "")
        lines.append(f"**{icon} {compliance.upper()}** {badge}  `{std}`  ")
        lines.append(f"> {detail}  ")
        lines.append("")
    return "\n".join(lines)


def run_semantic_check_ui(docs: list, row_idx: int | None) -> str:
    """
    Documents tab — 'Check against company standards' button handler.

    One LLM call, on demand, for the selected document only.
    Returns Markdown to display in the semantic result panel.
    """
    # ── STEP 0: gate ──────────────────────────────────────────────────────────
    logger.info(f"[semantic UI] button clicked — semantic_enabled={settings.semantic_enabled} "
                f"row_idx={row_idx!r} (type={type(row_idx).__name__}) "
                f"docs_count={len(docs) if docs else 0}")

    if not settings.semantic_enabled:
        return (
            "⚠️ **Semantic analysis is disabled.**  \n"
            "Set `SEMANTIC_ENABLED=true` in `.env` and restart to enable it."
        )

    # ── STEP 1: validate row selection ────────────────────────────────────────
    if not docs:
        logger.warning("[semantic UI] STEP 1 FAIL — docs_state is empty, no scan has been run")
        return "⚠️ **Run a folder or upload scan first** (Scan tab), then select a row here."

    # If no row was explicitly selected, fall back to the first flagged document
    if row_idx is None:
        flagged = [i for i, d in enumerate(docs) if d.get("needs_supervision")]
        row_idx = flagged[0] if flagged else 0
        logger.info(f"[semantic UI] STEP 1 — no row selected, falling back to row {row_idx}")

    try:
        row_idx = int(row_idx)
    except (TypeError, ValueError) as exc:
        logger.warning(f"[semantic UI] STEP 1 FAIL — cannot cast row_idx {row_idx!r}: {exc}")
        return "_Invalid row selection._"

    if row_idx >= len(docs):
        logger.warning(f"[semantic UI] STEP 1 FAIL — row_idx={row_idx} >= docs_len={len(docs)}")
        return "_Row index out of range — re-run the audit and select a row._"

    doc      = docs[row_idx]
    doc_name = doc.get("name", "?")
    doc_type = doc.get("doc_type", "unknown")
    doc_keys = list(doc.keys())
    logger.info(f"[semantic UI] STEP 1 OK — doc={doc_name!r} type={doc_type!r} keys={doc_keys}")

    # ── STEP 2: get text ──────────────────────────────────────────────────────
    doc_text = doc.get("text", "") or ""
    logger.info(f"[semantic UI] STEP 2 — text from doc dict: {len(doc_text)} chars")

    # Fallback: re-extract if text was not stored in the audit result
    if not doc_text.strip():
        path = doc.get("path", "")
        logger.info(f"[semantic UI] STEP 2 — text empty, trying re-extract from path={path!r}")
        if path:
            try:
                from domain.tools.extractor import extract_document
                doc_text = extract_document(path).get("text", "") or ""
                logger.info(f"[semantic UI] STEP 2 — re-extracted {len(doc_text)} chars")
            except Exception as exc:
                logger.warning(f"[semantic UI] STEP 2 — re-extract failed: {exc}")

    if not doc_text.strip():
        logger.warning(f"[semantic UI] STEP 2 FAIL — no text for {doc_name!r}")
        return f"❌ `{doc_name}` has no extractable text — semantic check cannot run."

    logger.info(f"[semantic UI] STEP 2 OK — {len(doc_text)} chars, excerpt[:80]={doc_text[:80]!r}")

    # ── STEP 3: semantic_check (with hard timeout to prevent UI freeze) ──────
    logger.info(f"[semantic UI] STEP 3 — calling semantic_check(doc_type={doc_type!r})")
    import concurrent.futures as _cf
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            _fut = _pool.submit(semantic_check, doc_text, doc_type)
            try:
                findings = _fut.result(timeout=30)  # 30s hard cap
            except _cf.TimeoutError:
                logger.warning("[semantic UI] STEP 3 TIMEOUT after 30s")
                findings = []
    except Exception as exc:
        logger.warning(f"[semantic UI] STEP 3 EXCEPTION: {exc}")
        findings = []

    logger.info(f"[semantic UI] STEP 3 — semantic_check returned {len(findings)} finding(s)")

    if not findings:
        return (
            f"⚠️ **Semantic check returned no results** for `{doc_name}`.  \n"
            "ChromaDB may be empty, the model could not complete, or "
            "`SEMANTIC_ENABLED` is not set. Check terminal logs for `[semantic]` lines."
        )

    return _semantic_result_md(doc_name, findings)


# ── Audit run & toggle handlers ───────────────────────────────────────────────

def _get_file_path(f) -> str:
    """Normalize file object across Gradio versions."""
    if isinstance(f, str):
        return f
    if hasattr(f, "name"):
        return f.name
    if isinstance(f, dict):
        return f.get("path") or f.get("name") or f.get("tmp_path") or ""
    return str(f)


# Return shape for both scan handlers:
# (audit_state, docs_state, hero, stats, reasons_fig, doc_type_fig,
#  table_rows, detail_md, source_label)
# IMPORTANT: gr.Plot with value=None stays in "processing" forever in Gradio 6.
# Always return an empty Figure, never None, for plot outputs.
_EMPTY_FIG = go.Figure().update_layout(title="")
_EMPTY_RESULT = (None, [], "", "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")


def _do_audit(target: str, extrapolate: bool, source_label: str) -> tuple:
    """
    Shared audit engine used by both scan handlers.

    COST GUARANTEE: calls audit_and_persist() which wraps audit_repository() —
    a 100% deterministic Python pipeline with ZERO LLM calls regardless of
    folder size or file count. DB writes are fire-and-forget; a DB outage
    never breaks the scan. The agent/semantic layer is NEVER invoked here.

    Always returns the full 9-tuple even on internal failure — gr.Plot outputs
    are ALWAYS valid Figure objects, never None (None leaves plots in
    "processing" forever in Gradio 6).
    """
    try:
        result      = audit_and_persist(target)
        documents   = result.get("documents", [])
        docs_sorted = sorted(documents, key=lambda d: d.get("trust_score", 1.0))

        total_docs = result.get("headline", {}).get("total_documents", 0)
        if total_docs >= LARGE_FOLDER_WARN:
            result["_large_folder_warning"] = (
                f"⚠️ Large folder: {total_docs:,} files scanned. "
                "Results are complete — no files were skipped."
            )

        reasons  = _reasons_fig(result)
        doc_type = _doc_type_fig(result)

        return (
            result,
            docs_sorted,
            _hero_html(result, extrapolate),
            _stats_html(result, extrapolate),
            reasons  if reasons  is not None else _EMPTY_FIG,
            doc_type if doc_type is not None else _EMPTY_FIG,
            _build_table_rows(docs_sorted),
            "_Select a row to see document details._",
            source_label,
        )
    except Exception as exc:
        logger.exception(f"[_do_audit] INTERNAL ERROR for {target!r}: {exc}")
        return (
            None, [],
            f"❌ **Audit error:** {exc}", "",
            _EMPTY_FIG, _EMPTY_FIG,
            [], "_No data._", source_label,
        )


def run_folder_audit(folder: str, extrapolate: bool) -> tuple:
    """Section 1 handler: scan a local / synced folder. Ignores any uploads."""
    perm = check_action(resolve_role(), "audit", identity="ui")
    if not perm.allowed:
        return (None, [], f"⚠️ {perm.reason}", "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")

    logger.info(f"[run_folder_audit] START folder={folder!r}")
    target = (folder or DEFAULT_FOLDER).strip()
    if not os.path.isdir(target):
        err = f"❌ Folder not found: `{target}`"
        logger.warning(f"[run_folder_audit] END (folder not found)")
        return (None, [], err, "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")
    try:
        result = _do_audit(target, extrapolate,
                           source_label=f"📁 **Results for folder:** `{target}`")
        logger.info(f"[run_folder_audit] END OK docs={result[0]['headline']['total_documents'] if result[0] else 0}")
        return result
    except Exception as exc:
        logger.exception(f"[run_folder_audit] END ERROR: {exc}")
        return (None, [], f"❌ **Error:** {exc}", "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")


def run_upload_audit(uploaded_files, extrapolate: bool) -> tuple:
    """Section 2 handler: audit only the uploaded file(s). Ignores folder path."""
    perm = check_action(resolve_role(), "upload", identity="ui")
    if not perm.allowed:
        return (None, [], f"⚠️ {perm.reason}", "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")

    logger.info(f"[run_upload_audit] START files={len(uploaded_files) if uploaded_files else 0}")
    if not uploaded_files:
        msg = "⚠️ **Please upload at least one document before scanning.**"
        return (None, [], msg, "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="sota_audit_")
        copied: list[str] = []
        for f in uploaded_files:
            src = _get_file_path(f)
            if src and os.path.isfile(src):
                shutil.copy(src, tmp_dir)
                copied.append(os.path.basename(src))

        if not copied:
            return (None, [], "❌ No readable files found in the upload.", "",
                    _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")

        n = len(copied)
        names_md = ", ".join(f"`{c}`" for c in copied[:3])
        if n > 3:
            names_md += f" +{n - 3} more"
        label = f"📤 **Results for {n} uploaded file(s):** {names_md}"
        result = _do_audit(tmp_dir, extrapolate, source_label=label)
        logger.info(f"[run_upload_audit] END OK files={n}")
        return result

    except Exception as exc:
        logger.exception(f"[run_upload_audit] END ERROR: {exc}")
        return (None, [], f"❌ **Error:** {exc}", "", _EMPTY_FIG, _EMPTY_FIG, [], "_No data._", "")
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def update_extrapolation(extrapolate: bool, result: dict | None):
    if not result:
        return "", ""
    return _hero_html(result, extrapolate), _stats_html(result, extrapolate)


def select_doc_row(evt: gr.SelectData, docs: list) -> str:
    if evt is None or evt.index is None:
        return "_Select a row to see details._"
    row_idx = evt.index[0] if isinstance(evt.index, (list, tuple)) else int(evt.index)
    return _doc_detail_md(row_idx, docs)


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — chat helpers (unchanged logic from original app.py)
# ─────────────────────────────────────────────────────────────────────────────

def chat(
    message: str,
    history: list,
    thread_id: str,
    enable_hitl: bool,
) -> Iterator[tuple[list, str, gr.update, gr.update, gr.update]]:
    """Yields: (history, trace_md, hitl_row_visible, hitl_response_text, warnings_text)"""
    if not message.strip():
        yield history, "", gr.update(visible=False), gr.update(value=""), gr.update(value="")
        return

    guard = input_guard(message)
    warnings_text = ""
    if not guard.passed:
        history = history + [
            {"role": "user",      "content": message},
            {"role": "assistant", "content": f"⚠️ {guard.reason}"},
        ]
        yield history, "", gr.update(visible=False), gr.update(value=""), gr.update(value=f"🚫 {guard.reason}")
        return
    if guard.warnings:
        warnings_text = "⚠️ " + " | ".join(guard.warnings)

    history = history + [
        {"role": "user",      "content": message},
        {"role": "assistant", "content": ""},
    ]
    trace_lines: list[str] = []

    try:
        from observability import get_callbacks
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": get_callbacks(),
        }
        initial_state = {
            "messages":        [HumanMessage(content=guard.sanitised_text)],
            "next_agent":      "",
            "reasoning":       "",
            "last_specialist": "",
            "supervisor_rounds": 0,
            "critique":        "",
            "critique_score":  0.0,
            "should_revise":   False,
            "revision_count":  0,
            "retrieved_context": "",
            "hitl_required":   enable_hitl,
        }

        with _graph_lock:
            stream      = _graph.stream(initial_state, config=config, stream_mode="values")
            accumulated = ""

            for event in stream:
                msgs        = event.get("messages", [])
                reasoning   = event.get("reasoning", "")
                next_agent  = event.get("next_agent", "")
                score       = event.get("critique_score")
                critique    = event.get("critique", "")
                should_revise = event.get("should_revise", False)

                if reasoning and next_agent and next_agent not in ("", "FINISH"):
                    trace_lines.append(
                        f"🔀 **Supervisor** → `{next_agent}`\n   _{reasoning}_"
                    )

                if score is not None and score > 0:
                    icon = "🔁" if should_revise else "✅"
                    trace_lines.append(
                        f"{icon} **Critic** score=`{score:.0%}`"
                        + (f"\n   _{critique[:80]}_" if critique and should_revise else "")
                    )

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

        state = _graph.get_state(config)
        if state.next and "hitl" in str(state.next):
            interrupt_val  = state.tasks[0].interrupts[0].value if state.tasks else {}
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
    pending = _hitl_pending.pop(thread_id, None)
    if not pending:
        return [], "No pending HITL request."
    config = pending["config"]
    try:
        result = resume_after_hitl(_graph, thread_id, approved=approved, feedback=feedback)
        trace  = "✅ Approved and sent." if approved else f"🔁 Sent back for revision: {feedback}"
        return [{"role": "assistant", "content": result}], trace
    except Exception as e:
        logger.error(f"HITL resume error: {e}")
        return [], f"Error resuming: {e}"


def upload_docs(files, progress=gr.Progress()) -> str:
    if not files:
        return "No files selected."
    paths = [_get_file_path(f) for f in files]
    paths = [p for p in paths if p]
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


# ─────────────────────────────────────────────────────────────────────────────
# UI assembly
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Tab 4 — Analytics helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── ROI model constants ───────────────────────────────────────────────────────
_ROI_SMALL_CORPUS_TB      = 4.0      # small repository (TB)
_ROI_LARGE_CORPUS_TB      = 40.0     # large repository (TB)
_ROI_DOCS_PER_TB          = 7_500    # enterprise average (mixed Office files)
_ROI_WORKER_EUR_HR        = 40.0     # loaded knowledge-worker cost €/h
_ROI_AUDIT_MIN_DOC        = 3.0      # minutes to manually audit one document
_ROI_AUDITS_PER_YEAR      = 2        # compliance review cycles per year
_ROI_RAG_IN_INDEX         = 0.50     # fraction of flagged docs that land in RAG index
_ROI_RAG_Q_PER_DOC        = 4        # RAG queries/year that touch a flagged doc
_ROI_MISLEAD_RATE         = 0.25     # fraction of those queries that return a wrong answer
_ROI_FIX_MIN              = 20.0     # minutes to detect and correct one misleading answer
_ROI_STORAGE_EUR_PER_GB_MO = 0.18   # cloud storage pricing €/GB/month
_ROI_FLAG_RATE            = 0.40     # industry benchmark: ~40 % of docs fail compliance


def _roi_html(flag_rate: float = _ROI_FLAG_RATE) -> str:
    """ROI / savings panel — computed entirely from audit statistics, no DB needed."""

    def _calc(tb: float) -> dict:
        docs       = int(tb * _ROI_DOCS_PER_TB)
        flagged    = int(docs * flag_rate)
        gb_flagged = tb * 1_024 * flag_rate
        storage    = gb_flagged * _ROI_STORAGE_EUR_PER_GB_MO * 12
        audit_h    = docs * _ROI_AUDIT_MIN_DOC / 60
        audit      = audit_h * _ROI_WORKER_EUR_HR * _ROI_AUDITS_PER_YEAR
        misleading = int(flagged * _ROI_RAG_IN_INDEX * _ROI_RAG_Q_PER_DOC * _ROI_MISLEAD_RATE)
        rag        = misleading * _ROI_FIX_MIN / 60 * _ROI_WORKER_EUR_HR
        return dict(docs=docs, flagged=flagged, gb_flagged=gb_flagged,
                    storage=storage, audit_h=audit_h, audit=audit,
                    misleading=misleading, rag=rag, total=storage + audit + rag)

    def _fmt(v: float) -> str:
        if v >= 1_000_000: return f"€{v/1_000_000:.1f}M"
        if v >= 1_000:     return f"€{v/1_000:.0f}K"
        return f"€{v:.0f}"

    def _panel(label: str, tb: float, d: dict) -> str:
        return (
            '<div style="flex:1;min-width:280px;background:#fff;border:1px solid #e5e7eb;'
            'border-radius:12px;padding:20px;">'

            f'<div style="font-size:13px;font-weight:700;color:#6b7280;text-transform:uppercase;'
            f'letter-spacing:.08em;margin-bottom:5px;">{label} · {tb:.0f} TB</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-bottom:14px;">'
            f'~{d["docs"]:,} documents &nbsp;·&nbsp; {flag_rate*100:.0f}% flagged'
            f' &nbsp;·&nbsp; {d["gb_flagged"]:,.0f} GB at risk</div>'

            '<div style="background:#fef3c7;border-radius:8px;padding:12px;margin-bottom:8px;">'
            '<div style="font-size:11px;color:#92400e;font-weight:600;margin-bottom:2px;">'
            '🗄️ WASTED STORAGE COST</div>'
            f'<div style="font-size:24px;font-weight:700;color:#b45309;">{_fmt(d["storage"])}'
            '<span style="font-size:13px;font-weight:400;">/yr</span></div>'
            f'<div style="font-size:11px;color:#78350f;">'
            f'{d["gb_flagged"]:,.0f} GB stale content · €{_ROI_STORAGE_EUR_PER_GB_MO}/GB/month</div></div>'

            '<div style="background:#ede9fe;border-radius:8px;padding:12px;margin-bottom:8px;">'
            '<div style="font-size:11px;color:#5b21b6;font-weight:600;margin-bottom:2px;">'
            '⏱️ COMPLIANCE AUDIT LABOR REPLACED</div>'
            f'<div style="font-size:24px;font-weight:700;color:#6d28d9;">{_fmt(d["audit"])}'
            '<span style="font-size:13px;font-weight:400;">/yr</span></div>'
            f'<div style="font-size:11px;color:#4c1d95;">'
            f'{d["audit_h"]:,.0f} h/audit · {_ROI_AUDITS_PER_YEAR}× per year · automated in seconds</div></div>'

            '<div style="background:#dcfce7;border-radius:8px;padding:12px;margin-bottom:14px;">'
            '<div style="font-size:11px;color:#166534;font-weight:600;margin-bottom:2px;">'
            '🤖 RAG MISLEADING ANSWERS AVOIDED</div>'
            f'<div style="font-size:24px;font-weight:700;color:#15803d;">{_fmt(d["rag"])}'
            '<span style="font-size:13px;font-weight:400;">/yr</span></div>'
            f'<div style="font-size:11px;color:#14532d;">'
            f'{d["misleading"]:,} wrong AI answers avoided · {_ROI_FIX_MIN:.0f} min correction each</div></div>'

            '<div style="padding:14px;background:linear-gradient(135deg,#1e293b,#0f172a);'
            'border-radius:10px;text-align:center;">'
            '<div style="font-size:11px;color:#94a3b8;font-weight:600;letter-spacing:.06em;margin-bottom:4px;">'
            'TOTAL ANNUAL IMPACT</div>'
            f'<div style="font-size:34px;font-weight:800;color:#f8fafc;">{_fmt(d["total"])}</div></div>'
            '</div>'
        )

    o = _calc(_ROI_SMALL_CORPUS_TB)
    g = _calc(_ROI_LARGE_CORPUS_TB)
    assumptions = (
        f"{_ROI_DOCS_PER_TB:,} docs/TB · {flag_rate*100:.0f}% flag rate (Gartner industry avg) · "
        f"€{_ROI_WORKER_EUR_HR:.0f}/h loaded cost · {_ROI_AUDIT_MIN_DOC:.0f} min manual audit/doc · "
        f"{_ROI_AUDITS_PER_YEAR} cycles/yr · {int(_ROI_RAG_IN_INDEX*100)}% of flagged docs indexed in RAG"
    )
    return (
        '<div style="margin:16px 0 8px;">'
        '<div style="font-size:17px;font-weight:700;color:#111827;margin-bottom:2px;">'
        f'💶 Value at Stake — What {settings.app_name} saves</div>'
        '<div style="font-size:13px;color:#6b7280;margin-bottom:16px;">'
        'Cost of unmanaged document entropy across your repositories</div>'
        f'<div style="display:flex;gap:16px;flex-wrap:wrap;">{_panel("Small", _ROI_SMALL_CORPUS_TB, o)}{_panel("Large", _ROI_LARGE_CORPUS_TB, g)}</div>'
        f'<div style="margin-top:10px;font-size:11px;color:#9ca3af;font-style:italic;">📐 {assumptions}</div>'
        '</div>'
    )


def _analytics_kpi_html(s: dict) -> str:
    fix_color = "#16a34a" if s["fix_rate"] >= 70 else ("#d97706" if s["fix_rate"] >= 40 else "#dc2626")
    cards = [
        ("🗂️", str(s["total_runs"]),                      "Validation runs"),
        ("🔍", str(s["total_findings"]),                   "Total findings"),
        ("🔴", str(s["open"]),                             "Open"),
        ("✅", f'{s["fix_rate"]}%',                        "Fix rate", fix_color),
    ]
    inner = "".join(
        f'<div style="flex:1;min-width:110px;background:#f9fafb;border:1px solid #e5e7eb;'
        f'padding:14px 10px;border-radius:10px;text-align:center;">'
        f'<div style="font-size:22px;">{icon}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{c[3] if len(c)>3 else "#111827"};margin:4px 0;">{val}</div>'
        f'<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">{lbl}</div>'
        f'</div>'
        for c in cards for icon, val, lbl, *_ in [c]
    )
    return f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;">{inner}</div>'


def _severity_fig(rows: list[dict]):
    if not rows:
        return go.Figure().update_layout(title="No findings")
    color_map = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}
    labels  = [r["severity"] for r in rows]
    values  = [r["count"]    for r in rows]
    colors  = [color_map.get(s, "#9ca3af") for s in labels]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, marker_colors=colors,
        hole=0.45, textinfo="label+percent",
    ))
    fig.update_layout(
        title="Findings by severity", height=300,
        margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
    )
    return fig


def _status_fig(rows: list[dict]):
    if not rows:
        return go.Figure().update_layout(title="No findings")
    color_map = {"pending": "#ef4444", "fixed": "#22c55e",
                 "manual": "#3b82f6", "ignored": "#9ca3af",
                 "partial_fix": "#f59e0b", "pending_fix": "#a855f7"}
    labels = [r["status"] for r in rows]
    values = [r["count"]  for r in rows]
    colors = [color_map.get(s, "#6b7280") for s in labels]
    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=colors,
        text=values, textposition="outside",
    ))
    fig.update_layout(
        title="Findings by status", height=300,
        margin=dict(l=40, r=20, t=50, b=50),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(showgrid=True, gridcolor="#f3f4f6"),
    )
    return fig


def _rules_fig(rows: list[dict]):
    if not rows:
        return go.Figure().update_layout(title="No rule data")
    items = [(r["rule_code"], r["count"]) for r in rows]
    items.sort(key=lambda x: x[1])
    fig = go.Figure(go.Bar(
        x=[i[1] for i in items], y=[i[0] for i in items],
        orientation="h", marker_color="#6366f1",
        text=[str(i[1]) for i in items], textposition="outside",
    ))
    fig.update_layout(
        title="Top violation types", height=360,
        margin=dict(l=200, r=60, t=50, b=40),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12),
    )
    return fig


def _owners_fig(rows: list[dict]):
    if not rows:
        return go.Figure().update_layout(title="No owner data")
    owners   = [r["owner_username"] for r in rows]
    open_v   = [r["open"]     for r in rows]
    resolved = [r["resolved"] for r in rows]
    fig = go.Figure(data=[
        go.Bar(name="Open",     x=owners, y=open_v,   marker_color="#ef4444"),
        go.Bar(name="Resolved", x=owners, y=resolved, marker_color="#22c55e"),
    ])
    fig.update_layout(
        barmode="group", title="Findings by owner",
        height=300, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=60, b=50),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _timeline_fig(rows: list[dict]):
    if not rows:
        return go.Figure().update_layout(title="No run history yet")
    days = [r["day"]  for r in rows]
    runs = [r["runs"] for r in rows]
    fig = go.Figure(go.Scatter(
        x=days, y=runs, mode="lines+markers",
        line=dict(color="#6366f1", width=2),
        marker=dict(size=6),
        fill="tozeroy", fillcolor="rgba(99,102,241,0.15)",
    ))
    fig.update_layout(
        title="Validation runs over time", height=250,
        margin=dict(l=40, r=20, t=50, b=50),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#f3f4f6"),
    )
    return fig


def _savings_html(s: dict) -> str:
    """Hero dashboard card — white design, JS countup animation on every render."""
    if not s or s.get("total_runs", 0) == 0:
        return (
            '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;'
            'padding:32px;text-align:center;">'
            '<div style="font-size:32px;margin-bottom:8px;">📊</div>'
            f'<div style="font-size:15px;font-weight:600;color:#374151;margin-bottom:4px;">{settings.app_name} Impact Dashboard</div>'
            '<div style="font-size:13px;color:#9ca3af;">Run a scan to populate live metrics</div>'
            '</div>'
        )

    runs     = s["total_runs"]
    findings = s["total_findings"]
    fixed    = s.get("fixed",   0)
    manual   = s.get("manual",  0)
    ignored  = s.get("ignored", 0)
    resolved = fixed + manual + ignored
    fix_rate = float(s.get("fix_rate", 0))

    audit_h   = runs  * _ROI_AUDIT_MIN_DOC / 60
    audit_eur = audit_h * _ROI_WORKER_EUR_HR
    fix_h     = fixed * _ROI_FIX_MIN / 60
    fix_eur   = fix_h * _ROI_WORKER_EUR_HR
    total_eur = audit_eur + fix_eur
    total_min = (audit_h + fix_h) * 60        # total minutes for animation
    rag_saved = int(findings * _ROI_RAG_IN_INDEX * _ROI_RAG_Q_PER_DOC * _ROI_MISLEAD_RATE)

    fix_color = "#16a34a" if fix_rate >= 70 else ("#d97706" if fix_rate >= 40 else "#dc2626")

    # Static formatted labels (shown in sublabels, not animated)
    def _fe(v: float) -> str:
        if v >= 1_000_000: return f"€{v/1_000_000:.1f}M"
        if v >= 1_000:     return f"€{v/1_000:.1f}K"
        return f"€{int(v)}"

    def _fmin(m: float) -> str:
        if m >= 60: return f"{m/60:.1f}h"
        return f"{int(m)}min"

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'background:#fff;border:1px solid #e5e7eb;border-radius:16px;'
        'padding:24px 26px;box-shadow:0 1px 4px rgba(0,0,0,.06);">'

        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">'
        '<div>'
        f'<div style="font-size:17px;font-weight:700;color:#111827;letter-spacing:-.01em;">📊 {settings.app_name} Impact Dashboard</div>'
        '<div style="font-size:11px;color:#9ca3af;margin-top:3px;">Cumulative · updates every 8 s</div>'
        '</div>'
        '<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:20px;'
        'padding:5px 14px;font-size:11px;color:#16a34a;font-weight:600;">🟢 LIVE</div>'
        '</div>'

        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px;">'

        '<div style="border:1px solid #e5e7eb;border-radius:12px;padding:18px 16px;">'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">💶 Labor Cost Saved</div>'
        f'<div data-dave data-val="{round(total_eur)}" data-fmt="eur" '
        f'style="font-size:40px;font-weight:800;color:#16a34a;line-height:1;letter-spacing:-.02em;">{_fe(total_eur)}</div>'
        f'<div style="font-size:11px;color:#9ca3af;margin-top:6px;">audit {_fe(audit_eur)} · auto-fix {_fe(fix_eur)}</div>'
        '</div>'

        '<div style="border:1px solid #e5e7eb;border-radius:12px;padding:18px 16px;">'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">⏱️ Time Saved</div>'
        f'<div data-dave data-val="{round(total_min)}" data-fmt="min" '
        f'style="font-size:40px;font-weight:800;color:#2563eb;line-height:1;letter-spacing:-.02em;">{_fmin(total_min)}</div>'
        f'<div style="font-size:11px;color:#9ca3af;margin-top:6px;">vs. {_fmin(audit_h*60)} manual audit + {_fmin(fix_h*60)} fix</div>'
        '</div>'

        '<div style="border:1px solid #e5e7eb;border-radius:12px;padding:18px 16px;">'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;">🤖 RAG Errors Avoided</div>'
        f'<div data-dave data-val="{rag_saved}" data-fmt="int" '
        f'style="font-size:40px;font-weight:800;color:#7c3aed;line-height:1;letter-spacing:-.02em;">{rag_saved}</div>'
        '<div style="font-size:11px;color:#9ca3af;margin-top:6px;">misleading AI answers prevented</div>'
        '</div>'

        '</div>'

        '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">'

        '<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:14px;text-align:center;">'
        f'<div data-dave data-val="{runs}" data-fmt="int" style="font-size:26px;font-weight:700;color:#111827;">{runs}</div>'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-top:3px;">Docs audited</div>'
        '</div>'

        '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:14px;text-align:center;">'
        f'<div data-dave data-val="{findings}" data-fmt="int" style="font-size:26px;font-weight:700;color:#d97706;">{findings}</div>'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-top:3px;">Issues found</div>'
        '</div>'

        '<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:14px;text-align:center;">'
        f'<div data-dave data-val="{resolved}" data-fmt="int" style="font-size:26px;font-weight:700;color:#16a34a;">{resolved}</div>'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-top:3px;">Resolved</div>'
        '</div>'

        '<div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:14px;text-align:center;">'
        f'<div data-dave data-val="{fix_rate}" data-fmt="pct" style="font-size:26px;font-weight:700;color:{fix_color};">{fix_rate:.1f}%</div>'
        '<div style="font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-top:3px;">Fix rate</div>'
        '</div>'

        '</div>'
        '</div>'
    )


def run_savings_only() -> str:
    """Lightweight function for the gr.Timer — only refreshes the hero card."""
    if not settings.db_enabled:
        return ""
    try:
        get_stats = _chase_db()[0]
        return _savings_html(get_stats())
    except Exception:
        return ""


_ANALYTICS_DISABLED_HTML = (
    '<div style="padding:24px;background:#f9fafb;border:1px solid #e5e7eb;'
    'border-radius:10px;text-align:center;color:#6b7280;">'
    '<div style="font-size:32px;margin-bottom:8px;">📊</div>'
    '<div style="font-size:16px;font-weight:600;margin-bottom:6px;">'
    'Analytics not connected</div>'
    '<div style="font-size:13px;">Set <code>DB_ENABLED=true</code> and configure '
    '<code>DB_HOST</code> / <code>DB_NAME</code> / <code>DB_USER</code> / '
    '<code>DB_PASSWORD</code> in <code>.env</code> to enable the PostgreSQL '
    'analytics dashboard.</div></div>'
)


def run_analytics_ui():
    """Refresh handler for Tab 4 — queries Postgres and returns all chart data."""
    logger.info("[run_analytics_ui] START")
    _empty = (go.Figure(), go.Figure(), go.Figure(), go.Figure(), go.Figure())

    # Guard 1: analytics disabled via env flag — return immediately, no network call
    if not settings.db_enabled:
        logger.info("[run_analytics_ui] END (DB disabled)")
        return (_ANALYTICS_DISABLED_HTML, *_empty, "")

    # Guard 2: DB reachable but query failed — show error, never hang
    try:
        (get_stats, get_sev, get_status,
         get_rules, get_owners, get_timeline) = _chase_db()

        stats    = get_stats()
        sev      = get_sev()
        statuses = get_status()
        rules    = get_rules()
        owners   = get_owners()
        timeline = get_timeline()

        result = (
            _savings_html(stats),   # colleague's richer hero card with ROI metrics
            _severity_fig(sev),
            _status_fig(statuses),
            _rules_fig(rules),
            _owners_fig(owners),
            _timeline_fig(timeline),
            "",
        )
        logger.info("[run_analytics_ui] END OK")
        return result
    except Exception as exc:
        logger.warning(f"[run_analytics_ui] END ERROR: {exc}")
        err_html = (
            f'<div style="padding:16px;background:#fef2f2;border:1px solid #fecaca;'
            f'border-radius:8px;color:#991b1b;font-size:13px;">'
            f'⚠️ <strong>Database unreachable</strong><br>{exc}</div>'
        )
        return (err_html, *_empty, f"🔴 {exc}")


CHAT_EXAMPLES = [
    "What is LangGraph and how does the supervisor pattern work?",
    "Write Python to compute and plot a confusion matrix.",
    "Search for recent papers on agentic AI.",
    "Explain SHAP values with a code example using XGBoost.",
    "Remember that I prefer pandas for data manipulation.",
    "Audit domain/demo_corpus/files and tell me what to prioritise.",
]

TABLE_HEADERS = ["Name", "Type", "Size", "Modified", "Trust", "Supervision?", "Top reason"]


_DAVE_ANIMATE_JS = """
function auditAnimate() {
  var D = 1400;
  function ease(t) { return 1 - Math.pow(1 - t, 3); }
  document.querySelectorAll('[data-dave]').forEach(function(el) {
    var target = parseFloat(el.getAttribute('data-val') || '0');
    var fmt    = el.getAttribute('data-fmt') || 'int';
    function fv(v) {
      if (fmt === 'eur') { v = Math.round(v); return v >= 1000 ? '€' + (v/1000).toFixed(1) + 'K' : '€' + v; }
      if (fmt === 'min') { v = Math.round(v); return v >= 60 ? (v/60).toFixed(1) + 'h' : v + 'min'; }
      if (fmt === 'pct') return v.toFixed(1) + '%';
      return '' + Math.round(v);
    }
    var t0 = performance.now();
    (function tick(now) {
      var p = Math.min((now - t0) / D, 1);
      el.textContent = fv(target * ease(p));
      if (p < 1) requestAnimationFrame(tick);
    })(t0);
  });
}
"""

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="AI-Readiness Dashboard") as demo:

        gr.Markdown(
            "# AI-Readiness Dashboard\n"
            "**Auditor · LangGraph · Reflexion · HITL · RAG · Mem0**"
        )

        # ── Shared state (accessible from all tabs) ───────────────────────────
        audit_state       = gr.State(None)   # full audit result dict
        docs_state        = gr.State([])     # per-document list sorted by trust_score
        selected_doc_idx  = gr.State(None)   # row index of the selected document (Tab 2)

        with gr.Tabs():

            # ═══════════════════════════════════════════════════════════════════
            # TAB 1 — SCAN
            # ═══════════════════════════════════════════════════════════════════
            with gr.Tab("🔍 Scan"):

                # ═══════════════════════════════════════════════════════════════
                # SECTION 1 — Scan a folder
                # ═══════════════════════════════════════════════════════════════
                gr.Markdown("### 📁 Scan a folder")

                with gr.Row():
                    folder_input = gr.Textbox(
                        value=DEFAULT_FOLDER,
                        label="Folder path",
                        placeholder="Absolute or relative path to your document repository",
                        scale=4,
                    )
                    scan_folder_btn = gr.Button(
                        "📁 Scan Folder", variant="primary", scale=1, min_width=150
                    )

                gr.Markdown("---")

                # ═══════════════════════════════════════════════════════════════
                # SECTION 2 — Scan uploaded document(s)
                # ═══════════════════════════════════════════════════════════════
                gr.Markdown("### 📤 Scan uploaded document(s)")

                with gr.Row():
                    with gr.Column(scale=4):
                        file_upload_scan = gr.File(
                            label="Drag & drop files here (PDF, DOCX, XLSX, PPTX, "
                                  "CSV, TXT, MD) — add or remove individually",
                            file_count="multiple",
                            file_types=[
                                ".pdf", ".docx", ".xlsx", ".pptx",
                                ".csv", ".txt", ".md", ".json",
                            ],
                        )
                    with gr.Column(scale=1, min_width=160):
                        scan_upload_btn = gr.Button(
                            "📤 Scan Uploaded", variant="secondary", min_width=150
                        )

                gr.Markdown("---")

                # ═══════════════════════════════════════════════════════════════
                # SHARED OPTIONS + RESULTS
                # ═══════════════════════════════════════════════════════════════
                extrapolate_cb = gr.Checkbox(
                    label=f"Extrapolate to full repository (×{EXTRAPOLATION_FACTOR:,}) — "
                          "scales document count & remediation hours for illustration only",
                    value=False,
                )

                # Source label — updates after each scan to show what was audited
                scan_source_md = gr.Markdown(value="")

                # Hero + stat cards
                hero_html  = gr.HTML(value="")
                stats_html = gr.HTML(value="")

                # Charts
                with gr.Row():
                    reasons_plot  = gr.Plot(label="Top reasons for flagging")
                    doc_type_plot = gr.Plot(label="By document type")

            # ═══════════════════════════════════════════════════════════════════
            # TAB 2 — DOCUMENTS
            # ═══════════════════════════════════════════════════════════════════
            with gr.Tab("📋 Documents"):

                gr.Markdown(
                    "Sorted by trust score (worst first). "
                    "**Click a row** to see the full findings for that document."
                )

                doc_table = gr.Dataframe(
                    headers=TABLE_HEADERS,
                    datatype=["str"] * len(TABLE_HEADERS),
                    interactive=False,
                    wrap=True,
                    row_count=(1, "dynamic"),
                )

                gr.Markdown("---\n### Document detail")
                detail_md = gr.Markdown("_Run an audit in the Scan tab, then select a row._")

                gr.Markdown("---")

                # ── Semantic compliance check (on-demand, one LLM call) ───────
                _sem_label = (
                    "🧠 Check against company standards (semantic)"
                    if settings.semantic_enabled
                    else "🧠 Semantic analysis disabled (set SEMANTIC_ENABLED=true)"
                )
                semantic_btn = gr.Button(
                    _sem_label,
                    variant="secondary" if settings.semantic_enabled else "stop",
                    size="sm",
                )
                semantic_md = gr.Markdown(
                    value=(
                        "**Semantic compliance check** — click the button above after running "
                        "a scan. If no row is selected, the first flagged document is used automatically."
                    )
                )

            # ═══════════════════════════════════════════════════════════════════
            # TAB 3 — ASK THE AGENT
            # ═══════════════════════════════════════════════════════════════════
            with gr.Tab("🤖 Ask the agent"):

                with gr.Row():
                    # ── Left: Chat ────────────────────────────────────────────
                    with gr.Column(scale=3):
                        chatbot = gr.Chatbot(height=500)

                        # HITL approval panel
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
                                approve_btn = gr.Button("✅ Approve",         variant="primary")
                                reject_btn  = gr.Button("🔁 Reject & revise", variant="secondary")

                        warnings_box = gr.Markdown(value="", visible=True)

                        # MCP elicitation panel (shown when a server requests input)
                        with gr.Group(visible=False) as elicit_row:
                            gr.Markdown("### 🔌 MCP Server Input Required")
                            elicit_msg    = gr.Markdown(value="")
                            elicit_id     = gr.State("")
                            elicit_mode   = gr.State("form")
                            elicit_fields = gr.Textbox(
                                label="Response (JSON key/value pairs or URL confirmation)",
                                placeholder='{"field_name": "value", ...}',
                                lines=4,
                            )
                            with gr.Row():
                                elicit_submit_btn  = gr.Button("✅ Submit",  variant="primary")
                                elicit_decline_btn = gr.Button("🚫 Decline", variant="secondary")
                        elicit_timer = gr.Timer(value=2)

                        with gr.Row():
                            msg_box  = gr.Textbox(
                                placeholder="Ask anything — audits, code, research…",
                                label="", lines=2, scale=5,
                            )
                            send_btn = gr.Button("Send ▶", variant="primary", scale=1)

                        gr.Examples(examples=CHAT_EXAMPLES, inputs=msg_box, label="Examples")

                    # ── Right: Controls + Trace + Memory ─────────────────────
                    with gr.Column(scale=1):
                        hitl_toggle = gr.Checkbox(
                            label="🔒 Enable human-in-the-loop review",
                            value=False,
                        )

                        thread_state   = gr.State(new_session())
                        thread_display = gr.Textbox(
                            label="Session ID", interactive=False, value=new_session()
                        )
                        new_session_btn = gr.Button("🔄 New session")

                        langfuse_note = (
                            "🔍 [Langfuse trace active](https://cloud.langfuse.com) — "
                            "every decision is logged and auditable."
                            if settings.langfuse_public_key
                            else "_Langfuse tracing not configured (`LANGFUSE_PUBLIC_KEY` unset)._"
                        )
                        gr.Markdown(f"---\n{langfuse_note}")

                        gr.Markdown("---\n### 🔍 Agent trace")
                        trace_box = gr.Markdown(value="_(trace will appear here)_")

                        gr.Markdown("---\n### 🧠 Episodic memory")
                        recall_btn = gr.Button("Show memories")
                        memory_box = gr.Markdown(value="")

                        gr.Markdown("---\n### 📄 Add to RAG memory")
                        file_upload_rag = gr.File(
                            label=".txt / .md / .pdf",
                            file_types=[".txt", ".md", ".pdf"],
                            file_count="multiple",
                        )
                        upload_btn    = gr.Button("📥 Ingest")
                        upload_status = gr.Textbox(label="Status", interactive=False)

            # ═══════════════════════════════════════════════════════════════════
            # TAB 4 — ANALYTICS
            # ═══════════════════════════════════════════════════════════════════
            with gr.Tab("📊 Analytics") as analytics_tab:

                analytics_timer = gr.Timer(value=8)

                with gr.Row():
                    analytics_refresh_btn = gr.Button("🔄 Refresh all", variant="primary", scale=0, min_width=140)
                    analytics_status      = gr.Markdown(value="")

                analytics_savings = gr.HTML(value="")

                with gr.Row():
                    analytics_severity_plot = gr.Plot(label="")
                    analytics_status_plot   = gr.Plot(label="")

                with gr.Row():
                    analytics_rules_plot  = gr.Plot(label="")
                    analytics_owners_plot = gr.Plot(label="")

                analytics_timeline_plot = gr.Plot(label="")

        # ── Wiring ────────────────────────────────────────────────────────────

        # ── Tab 1 — Scan Folder button ────────────────────────────────────────
        _scan_outputs = [
            audit_state, docs_state,
            hero_html, stats_html,
            reasons_plot, doc_type_plot,
            doc_table, detail_md,
            scan_source_md,
        ]
        scan_folder_btn.click(
            run_folder_audit,
            inputs=[folder_input, extrapolate_cb],
            outputs=_scan_outputs,
        )

        # ── Tab 1 — Scan Uploaded button ──────────────────────────────────────
        scan_upload_btn.click(
            run_upload_audit,
            inputs=[file_upload_scan, extrapolate_cb],
            outputs=_scan_outputs,
        )

        # ── Tab 1 — extrapolation toggle re-renders without re-running audit ──
        extrapolate_cb.change(
            update_extrapolation,
            inputs=[extrapolate_cb, audit_state],
            outputs=[hero_html, stats_html],
        )

        # Tab 2 — row selection → detail panel + record selected index
        def _select_and_track(evt: gr.SelectData, docs: list):
            raw = evt.index
            row_idx = raw[0] if isinstance(raw, (list, tuple)) else int(raw)
            logger.info(f"[semantic UI] row selected — evt.index={raw!r} → row_idx={row_idx}, "
                        f"docs_count={len(docs) if docs else 0}")
            return _doc_detail_md(row_idx, docs), row_idx

        doc_table.select(
            _select_and_track,
            inputs=[docs_state],
            outputs=[detail_md, selected_doc_idx],
        )

        # Tab 2 — semantic compliance button
        semantic_btn.click(
            run_semantic_check_ui,
            inputs=[docs_state, selected_doc_idx],
            outputs=[semantic_md],
        )

        # Tab 3 — chat
        def submit(msg, hist, tid, hitl):
            yield from chat(msg, hist, tid, hitl)

        send_btn.click(
            submit,
            inputs=[msg_box, chatbot, thread_state, hitl_toggle],
            outputs=[chatbot, trace_box, hitl_row, hitl_response, warnings_box],
        ).then(lambda: "", outputs=msg_box)

        msg_box.submit(
            submit,
            inputs=[msg_box, chatbot, thread_state, hitl_toggle],
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
            reset,
            outputs=[thread_state, thread_display, chatbot, trace_box, memory_box],
        )

        recall_btn.click(show_memories,  inputs=thread_state,   outputs=memory_box)
        upload_btn.click(upload_docs,    inputs=file_upload_rag, outputs=upload_status)

        # MCP elicitation polling + response handlers
        def _poll_elicitation():
            pending = get_pending()
            if not pending:
                return (
                    gr.update(visible=False), "", "", "form", "",
                )
            req = pending[0]
            if req.mode == "form":
                props = req.schema.get("properties", req.schema)
                field_hints = "\n".join(
                    f'  "{k}": ""   # {v.get("description", v.get("type", ""))} '
                    for k, v in props.items()
                ) if props else ""
                placeholder = "{\n" + field_hints + "\n}" if field_hints else "{}"
            else:
                placeholder = f"Visit this URL and click Submit when done:\n{req.url}"
            msg_md = f"**{req.message}**"
            return (
                gr.update(visible=True),
                msg_md,
                req.request_id,
                req.mode,
                placeholder,
            )

        elicit_timer.tick(
            _poll_elicitation,
            outputs=[elicit_row, elicit_msg, elicit_id, elicit_mode, elicit_fields],
        )

        def _submit_elicitation(request_id, raw_fields, mode):
            import json
            if mode == "url":
                submit_response(request_id, {})
            else:
                try:
                    data = json.loads(raw_fields) if raw_fields.strip() else {}
                except json.JSONDecodeError:
                    data = {"value": raw_fields}
                submit_response(request_id, data)
            return gr.update(visible=False), "", ""

        def _decline_elicitation(request_id):
            decline_elicitation(request_id)
            return gr.update(visible=False), "", ""

        elicit_submit_btn.click(
            _submit_elicitation,
            inputs=[elicit_id, elicit_fields, elicit_mode],
            outputs=[elicit_row, elicit_msg, elicit_fields],
        )
        elicit_decline_btn.click(
            _decline_elicitation,
            inputs=[elicit_id],
            outputs=[elicit_row, elicit_msg, elicit_fields],
        )

        # Tab 4 — Analytics
        _analytics_outputs = [
            analytics_savings,
            analytics_severity_plot, analytics_status_plot,
            analytics_rules_plot,    analytics_owners_plot,
            analytics_timeline_plot,
            analytics_status,
        ]
        _animate_js = "() => { if(typeof auditAnimate==='function') auditAnimate(); }"
        analytics_refresh_btn.click(run_analytics_ui, outputs=_analytics_outputs).then(fn=None, js=_animate_js)
        analytics_tab.select(run_analytics_ui,         outputs=_analytics_outputs).then(fn=None, js=_animate_js)
        analytics_timer.tick(run_savings_only,          outputs=[analytics_savings])

        # Queue: required so every handler runs in a worker thread, never on the
        # event loop. max_size=20 prevents runaway queuing; concurrency_limit=4
        # lets scan/semantic/chat run in parallel (not serially behind each other).
        demo.queue(max_size=20, default_concurrency_limit=4)

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860, js=_DAVE_ANIMATE_JS)
