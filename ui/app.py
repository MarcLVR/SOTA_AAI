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
from domain.run_audit import audit_repository
from domain.tools.aggregate import human_bytes
from graph.workflow import build_graph, run_query, resume_after_hitl
from memory import ingest_documents, get_all_memories
from guardrails import input_guard


# ── EDS design tokens & chart palette ────────────────────────────────────────

_EDS = {
    "background":     "#F6F7F8",
    "surface":        "#FFFFFF",
    "foreground":     "#0D1116",
    "body":           "#1E2934",
    "subhead":        "#3D546A",
    "muted":          "#92A9C0",
    "primary":        "#0066AA",
    "primary_hover":  "#004C80",
    "primary_accent": "#D8E4F4",
    "primary_fg":     "#FFFFFF",
    "success":        "#20AD69",
    "success_accent": "#C6F3DF",
    "warning":        "#E3C041",
    "warning_accent": "#FBF4CC",
    "danger":         "#970125",
    "danger_accent":  "#FCDAD7",
    "divider":        "#3D546A",
    "card_border":    "#1E2934",
}

_EDS_CHART = [
    "#7DA6DB", "#888AE1", "#F488CF", "#CA96E3",
    "#D75684", "#FF7E7F", "#FF8E4C", "#FFA600",
]

_EDS_FONT = "-apple-system, 'Segoe UI', Roboto, sans-serif"


def _eds_fig(fig, **layout_extra):
    """Apply EDS tokens to any Plotly figure — transparent bg, correct fonts/gridlines."""
    axis = dict(
        gridcolor="rgba(61,84,106,0.15)",
        linecolor="rgba(61,84,106,0.3)",
        tickcolor="rgba(61,84,106,0.3)",
        zerolinecolor="rgba(61,84,106,0.2)",
        tickfont=dict(color=_EDS["body"], family=_EDS_FONT, size=11),
        titlefont=dict(color=_EDS["subhead"], family=_EDS_FONT, size=12),
    )
    fig.update_layout(
        font=dict(family=_EDS_FONT, color=_EDS["body"], size=12),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=_EDS_CHART,
        xaxis=axis, yaxis=axis,
        title_font=dict(family=_EDS_FONT, color=_EDS["foreground"], size=14, weight="bold"),
        legend=dict(font=dict(family=_EDS_FONT, color=_EDS["body"], size=11)),
        **layout_extra,
    )
    return fig


_EDS_CSS = """
/* ══════════════════════════════════════════════════════════════
   DAVE Enterprise Design System — CSS tokens + overrides
   ══════════════════════════════════════════════════════════════ */

:root {
  --eds-background:     #F6F7F8;
  --eds-surface:        #FFFFFF;
  --eds-foreground:     #0D1116;
  --eds-body:           #1E2934;
  --eds-subhead:        #3D546A;
  --eds-muted:          #92A9C0;
  --eds-primary:        #0066AA;
  --eds-primary-hover:  #004C80;
  --eds-primary-accent: #D8E4F4;
  --eds-primary-fg:     #FFFFFF;
  --eds-success:        #20AD69;
  --eds-success-accent: #C6F3DF;
  --eds-warning:        #E3C041;
  --eds-warning-accent: #FBF4CC;
  --eds-danger:         #970125;
  --eds-danger-accent:  #FCDAD7;
  --eds-divider:        #3D546A;
  --eds-card-border:    #1E2934;
  --eds-font:           -apple-system, "Segoe UI", Roboto, sans-serif;
  --eds-shadow-card:    0px 1px 10px 0px rgba(0,0,0,0.12),
                        0px 4px 5px  0px rgba(0,0,0,0.14),
                        0px 2px 4px -1px rgba(0,0,0,0.2);
  --eds-shadow-focus:   0px 0px 0px 2px rgba(115,157,211,0.2);
  --eds-shadow-dropdown:0px 3px 14px 2px rgba(0,0,0,0.12),
                        0px 8px 10px 1px rgba(0,0,0,0.14),
                        0px 5px 5px -3px rgba(0,0,0,0.2);
}

/* ── Page & container ─────────────────────────────────────── */
body { background: var(--eds-background) !important; }
.gradio-container {
  background: var(--eds-background) !important;
  font-family: var(--eds-font) !important;
  color: var(--eds-body) !important;
  max-width: 1440px !important;
}
.contain, .app { background: var(--eds-background) !important; }

/* ── Typography ───────────────────────────────────────────── */
.gradio-container, .gradio-container p, .gradio-container span {
  font-family: var(--eds-font) !important;
}
.prose h1, h1 {
  font-size: 36px !important; font-weight: 500 !important;
  line-height: 1.2 !important; color: var(--eds-foreground) !important;
  margin-bottom: 4px !important;
}
.prose h2, h2 {
  font-size: 24px !important; font-weight: 600 !important;
  line-height: 1.2 !important; color: var(--eds-foreground) !important;
}
.prose h3, h3 {
  font-size: 18px !important; font-weight: 600 !important;
  line-height: 1.2 !important; color: var(--eds-subhead) !important;
}
.prose h4, h4 {
  font-size: 16px !important; font-weight: 600 !important;
  line-height: 1.2 !important; color: var(--eds-subhead) !important;
}
.prose p, .prose li {
  font-size: 14px !important; line-height: 1.5 !important;
  color: var(--eds-body) !important;
}
.prose strong, strong { color: var(--eds-foreground) !important; }
.prose em, em         { color: var(--eds-subhead) !important; }
.prose code, code, .prose pre code {
  background: var(--eds-primary-accent) !important;
  color: var(--eds-primary) !important;
  border-radius: 3px !important;
  padding: 1px 5px !important;
  font-size: 12px !important;
  border: none !important;
}
.prose a { color: var(--eds-primary) !important; }
.prose a:hover { color: var(--eds-primary-hover) !important; }
.prose hr { border-color: rgba(61,84,106,0.2) !important; }

/* ── PRIMARY BUTTONS — EDS Blue #0066AA ───────────────────── */
button.primary {
  background-color: var(--eds-primary) !important;
  color: var(--eds-primary-fg) !important;
  border: 1px solid var(--eds-primary) !important;
  border-radius: 3px !important;
  font-family: var(--eds-font) !important;
  font-size: 14px !important;
  font-weight: 600 !important;
  letter-spacing: 0.02em !important;
  box-shadow: none !important;
  transition: background-color 0.15s ease !important;
}
button.primary:hover {
  background-color: var(--eds-primary-hover) !important;
  border-color: var(--eds-primary-hover) !important;
  box-shadow: var(--eds-shadow-card) !important;
}
button.primary:focus-visible {
  box-shadow: var(--eds-shadow-focus) !important;
  outline: none !important;
}

/* ── SECONDARY BUTTONS ─────────────────────────────────────── */
button.secondary {
  background-color: var(--eds-surface) !important;
  color: var(--eds-primary) !important;
  border: 1px solid var(--eds-primary) !important;
  border-radius: 3px !important;
  font-family: var(--eds-font) !important;
  font-size: 14px !important;
  font-weight: 500 !important;
  transition: background-color 0.15s ease !important;
}
button.secondary:hover {
  background-color: var(--eds-primary-accent) !important;
}
/* Generic fallback for any unlabelled button */
button {
  border-radius: 3px !important;
  font-family: var(--eds-font) !important;
}

/* ── TABS ──────────────────────────────────────────────────── */
.tabs { background: var(--eds-surface) !important; }
.tab-nav {
  background: var(--eds-surface) !important;
  border-bottom: 1px solid rgba(61,84,106,0.25) !important;
  gap: 0 !important;
}
.tab-nav button {
  color: var(--eds-muted) !important;
  font-family: var(--eds-font) !important;
  font-size: 14px !important;
  font-weight: 500 !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  background: transparent !important;
  padding: 12px 20px !important;
  transition: color 0.15s, border-color 0.15s, background 0.15s !important;
}
.tab-nav button:hover {
  color: var(--eds-subhead) !important;
  background: var(--eds-primary-accent) !important;
}
.tab-nav button.selected,
.tab-nav button[aria-selected="true"] {
  color: var(--eds-primary) !important;
  border-bottom: 2px solid var(--eds-primary) !important;
  font-weight: 600 !important;
  background: transparent !important;
}

/* ── BLOCKS & PANELS ──────────────────────────────────────── */
.block {
  background: var(--eds-surface) !important;
  border-radius: 3px !important;
  border-color: rgba(30,41,52,0.1) !important;
}
/* Give blocks the card shadow only when they look like cards */
.form, .block.gr-group, .gr-box {
  background: var(--eds-surface) !important;
  border-radius: 3px !important;
  border: 1px solid rgba(30,41,52,0.1) !important;
  box-shadow: var(--eds-shadow-card) !important;
}

/* ── INPUTS & TEXTBOXES ───────────────────────────────────── */
.block input[type="text"],
.block input[type="number"],
.block input:not([type="checkbox"]):not([type="radio"]),
.block textarea,
input.gr-text-input,
textarea.gr-text-input {
  background: var(--eds-surface) !important;
  border: 1px solid var(--eds-divider) !important;
  border-radius: 3px !important;
  color: var(--eds-body) !important;
  font-family: var(--eds-font) !important;
  font-size: 14px !important;
  line-height: 1.5 !important;
  transition: border-color 0.15s, box-shadow 0.15s !important;
}
.block input:focus, .block textarea:focus,
input:focus, textarea:focus {
  border-color: var(--eds-primary) !important;
  box-shadow: var(--eds-shadow-focus) !important;
  outline: none !important;
}
input::placeholder, textarea::placeholder {
  color: var(--eds-muted) !important;
}

/* ── LABELS ────────────────────────────────────────────────── */
.block label > span,
.label-wrap span,
span.svelte-1b6s6xi {
  color: var(--eds-subhead) !important;
  font-size: 12px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.06em !important;
  font-family: var(--eds-font) !important;
}

/* ── CHECKBOXES ────────────────────────────────────────────── */
input[type="checkbox"] { accent-color: var(--eds-primary) !important; }

/* ── DATAFRAME / TABLE ─────────────────────────────────────── */
.table-wrap {
  background: var(--eds-surface) !important;
  border-radius: 3px !important;
  border: 1px solid rgba(30,41,52,0.1) !important;
  box-shadow: var(--eds-shadow-card) !important;
}
table th {
  background: var(--eds-background) !important;
  color: var(--eds-foreground) !important;
  font-family: var(--eds-font) !important;
  font-size: 11px !important;
  font-weight: 700 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.07em !important;
  border-bottom: 2px solid rgba(61,84,106,0.35) !important;
  padding: 8px 12px !important;
}
table td {
  border-bottom: 1px solid rgba(61,84,106,0.12) !important;
  color: var(--eds-body) !important;
  font-family: var(--eds-font) !important;
  font-size: 13px !important;
  padding: 7px 12px !important;
}
table tr:hover td {
  background: var(--eds-primary-accent) !important;
}

/* ── CHATBOT MESSAGES ──────────────────────────────────────── */
.message.user    { background: var(--eds-primary-accent) !important; }
.message.bot     { background: var(--eds-surface) !important; border: 1px solid rgba(30,41,52,0.1) !important; }
.message         { border-radius: 3px !important; color: var(--eds-body) !important; }
.message-content { color: var(--eds-body) !important; font-family: var(--eds-font) !important; }

/* ── FILE UPLOAD ───────────────────────────────────────────── */
.upload-container, .file-preview-holder {
  border: 1.5px dashed rgba(61,84,106,0.4) !important;
  border-radius: 3px !important;
  background: var(--eds-background) !important;
}

/* ── SCROLLBAR ─────────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--eds-background); }
::-webkit-scrollbar-thumb { background: var(--eds-muted); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--eds-subhead); }
"""


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
            f'<div style="font-size:12px;color:{_EDS["muted"]};margin-top:6px;'
            f'font-family:{_EDS_FONT};">'
            f'extrapolated ×{EXTRAPOLATION_FACTOR:,} — actual corpus data scaled for illustration'
            f'</div>'
        )

    if pct >= 50:
        color, bg = _EDS["danger"],  _EDS["danger_accent"]
    elif pct >= 30:
        color, bg = "#8a6e00",       _EDS["warning_accent"]
    else:
        color, bg = _EDS["success"], _EDS["success_accent"]

    return (
        f'<div style="text-align:center;padding:28px 20px;background:{bg};'
        f'border-radius:3px;margin:8px 0;'
        f'box-shadow:0px 1px 10px 0px rgba(0,0,0,0.12),0px 4px 5px 0px rgba(0,0,0,0.14);">'
        f'<div style="font-size:80px;font-weight:500;color:{color};line-height:1.05;'
        f'font-family:{_EDS_FONT};">'
        f'{pct}%</div>'
        f'<div style="font-size:19px;color:{_EDS["body"]};margin-top:8px;'
        f'font-family:{_EDS_FONT};">'
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
        f'<div style="flex:1;min-width:110px;background:{_EDS["surface"]};'
        f'border:1px solid rgba(30,41,52,0.1);padding:14px 10px;border-radius:3px;'
        f'text-align:center;box-shadow:0px 1px 10px 0px rgba(0,0,0,0.12),'
        f'0px 4px 5px 0px rgba(0,0,0,0.14);">'
        f'<div style="font-size:22px;">{icon}</div>'
        f'<div style="font-size:17px;font-weight:700;color:{_EDS["foreground"]};'
        f'margin:4px 0;font-family:{_EDS_FONT};">{val}</div>'
        f'<div style="font-size:11px;color:{_EDS["muted"]};text-transform:uppercase;'
        f'letter-spacing:.06em;font-family:{_EDS_FONT};">{lbl}</div></div>'
        for icon, val, lbl in cards
    )
    return f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin:10px 0;">{inner}</div>'


def _reasons_fig(result: dict):
    if not result:
        return None
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
        return _eds_fig(go.Figure(), title="No flagged documents")

    fig = go.Figure(go.Bar(
        x=[i[1] for i in items],
        y=[i[0] for i in items],
        orientation="h",
        marker_color=_EDS["danger"],
        text=[str(i[1]) for i in items],
        textposition="outside",
        textfont=dict(family=_EDS_FONT, color=_EDS["body"], size=11),
    ))
    return _eds_fig(fig,
        title="Top reasons for flagging",
        xaxis_title="Documents",
        height=360,
        margin=dict(l=210, r=60, t=50, b=40),
    )


def _doc_type_fig(result: dict):
    if not result:
        return None
    by_type = result.get("by_doc_type", {})
    if not by_type:
        return _eds_fig(go.Figure(), title="No document-type data")

    types   = list(by_type.keys())
    counts  = [v["count"]   for v in by_type.values()]
    flagged = [v["flagged"] for v in by_type.values()]

    fig = go.Figure(data=[
        go.Bar(name="Total",   x=types, y=counts,  marker_color=_EDS_CHART[0]),
        go.Bar(name="Flagged", x=types, y=flagged, marker_color=_EDS["danger"]),
    ])
    return _eds_fig(fig,
        barmode="group",
        title="By document type",
        xaxis_title="Type",
        yaxis_title="Count",
        height=300,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(family=_EDS_FONT, color=_EDS["body"], size=11)),
        margin=dict(l=50, r=20, t=60, b=50),
    )


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


def run_audit_ui(folder: str, uploaded_files, extrapolate: bool):
    """
    Tab 1 'Run Audit' handler.
    Returns: (audit_state, docs_state, hero, stats, reasons_fig, doc_type_fig,
              table_rows, detail_md)
    """
    tmp_dir = None
    try:
        if uploaded_files:
            tmp_dir = tempfile.mkdtemp(prefix="sota_audit_")
            for f in uploaded_files:
                src = _get_file_path(f)
                if src and os.path.isfile(src):
                    shutil.copy(src, tmp_dir)
            target = tmp_dir
        else:
            target = (folder or DEFAULT_FOLDER).strip()

        result      = audit_repository(target)
        documents   = result.get("documents", [])
        docs_sorted = sorted(documents, key=lambda d: d.get("trust_score", 1.0))

        return (
            result,
            docs_sorted,
            _hero_html(result, extrapolate),
            _stats_html(result, extrapolate),
            _reasons_fig(result),
            _doc_type_fig(result),
            _build_table_rows(docs_sorted),
            "_Select a row to see document details._",
        )
    except Exception as exc:
        logger.exception(f"Audit error: {exc}")
        err = f"❌ **Error:** {exc}"
        return (None, [], err, "", None, None, [], "_No data._")
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

def _analytics_kpi_html(s: dict) -> str:
    fix_color = (
        _EDS["success"] if s["fix_rate"] >= 70
        else ("8a6e00" if s["fix_rate"] >= 40 else _EDS["danger"])
    )
    cards = [
        ("🗂️", str(s["total_runs"]),    "Validation runs", _EDS["foreground"]),
        ("🔍", str(s["total_findings"]), "Total findings",  _EDS["foreground"]),
        ("🔴", str(s["open"]),           "Open",            _EDS["danger"]),
        ("✅", f'{s["fix_rate"]}%',      "Fix rate",        fix_color),
    ]
    inner = "".join(
        f'<div style="flex:1;min-width:110px;background:{_EDS["surface"]};'
        f'border:1px solid rgba(30,41,52,0.1);padding:14px 10px;border-radius:3px;'
        f'text-align:center;box-shadow:0px 1px 10px 0px rgba(0,0,0,0.12),'
        f'0px 4px 5px 0px rgba(0,0,0,0.14);">'
        f'<div style="font-size:22px;">{icon}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{val_color};'
        f'margin:4px 0;font-family:{_EDS_FONT};">{val}</div>'
        f'<div style="font-size:11px;color:{_EDS["muted"]};text-transform:uppercase;'
        f'letter-spacing:.06em;font-family:{_EDS_FONT};">{lbl}</div>'
        f'</div>'
        for icon, val, lbl, val_color in cards
    )
    return f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin:10px 0;">{inner}</div>'


def _severity_fig(rows: list[dict]):
    if not rows:
        return _eds_fig(go.Figure(), title="No findings")
    color_map = {
        "high":   _EDS["danger"],
        "medium": _EDS["warning"],
        "low":    _EDS["success"],
    }
    labels = [r["severity"] for r in rows]
    values = [r["count"]    for r in rows]
    colors = [color_map.get(s, _EDS["muted"]) for s in labels]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, marker_colors=colors,
        hole=0.45, textinfo="label+percent",
        textfont=dict(family=_EDS_FONT, color=_EDS["body"], size=11),
    ))
    return _eds_fig(fig,
        title="Findings by severity", height=300,
        margin=dict(l=20, r=20, t=50, b=20), showlegend=False,
    )


def _status_fig(rows: list[dict]):
    if not rows:
        return _eds_fig(go.Figure(), title="No findings")
    color_map = {
        "pending":     _EDS["danger"],
        "fixed":       _EDS["success"],
        "manual":      _EDS_CHART[0],
        "ignored":     _EDS["muted"],
        "partial_fix": _EDS["warning"],
        "pending_fix": _EDS_CHART[1],
    }
    labels = [r["status"] for r in rows]
    values = [r["count"]  for r in rows]
    colors = [color_map.get(s, _EDS["subhead"]) for s in labels]
    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=colors,
        text=values, textposition="outside",
        textfont=dict(family=_EDS_FONT, color=_EDS["body"], size=11),
    ))
    return _eds_fig(fig,
        title="Findings by status", height=300,
        margin=dict(l=40, r=20, t=50, b=50),
    )


def _rules_fig(rows: list[dict]):
    if not rows:
        return _eds_fig(go.Figure(), title="No rule data")
    items = [(r["rule_code"], r["count"]) for r in rows]
    items.sort(key=lambda x: x[1])
    fig = go.Figure(go.Bar(
        x=[i[1] for i in items], y=[i[0] for i in items],
        orientation="h", marker_color=_EDS_CHART[1],
        text=[str(i[1]) for i in items], textposition="outside",
        textfont=dict(family=_EDS_FONT, color=_EDS["body"], size=11),
    ))
    return _eds_fig(fig,
        title="Top violation types", height=360,
        margin=dict(l=200, r=60, t=50, b=40),
    )


def _owners_fig(rows: list[dict]):
    if not rows:
        return _eds_fig(go.Figure(), title="No owner data")
    owners   = [r["owner_username"] for r in rows]
    open_v   = [r["open"]     for r in rows]
    resolved = [r["resolved"] for r in rows]
    fig = go.Figure(data=[
        go.Bar(name="Open",     x=owners, y=open_v,   marker_color=_EDS["danger"]),
        go.Bar(name="Resolved", x=owners, y=resolved, marker_color=_EDS["success"]),
    ])
    return _eds_fig(fig,
        barmode="group", title="Findings by owner", height=300,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1,
                    font=dict(family=_EDS_FONT, color=_EDS["body"], size=11)),
        margin=dict(l=40, r=20, t=60, b=50),
    )


def _timeline_fig(rows: list[dict]):
    if not rows:
        return _eds_fig(go.Figure(), title="No run history yet")
    days = [r["day"]  for r in rows]
    runs = [r["runs"] for r in rows]
    fig = go.Figure(go.Scatter(
        x=days, y=runs, mode="lines+markers",
        line=dict(color=_EDS_CHART[0], width=2),
        marker=dict(size=6, color=_EDS_CHART[0]),
        fill="tozeroy",
        fillcolor=f"rgba(125,166,219,0.12)",
    ))
    return _eds_fig(fig,
        title="Validation runs over time", height=250,
        margin=dict(l=40, r=20, t=50, b=50),
        xaxis=dict(showgrid=False),
    )


_ANALYTICS_DISABLED_HTML = (
    f'<div style="padding:32px 24px;background:{_EDS["surface"]};'
    f'border:1px solid rgba(30,41,52,0.1);border-radius:3px;text-align:center;'
    f'box-shadow:0px 1px 10px 0px rgba(0,0,0,0.12),0px 4px 5px 0px rgba(0,0,0,0.14);">'
    f'<div style="font-size:32px;margin-bottom:8px;">📊</div>'
    f'<div style="font-size:16px;font-weight:600;margin-bottom:6px;'
    f'color:{_EDS["foreground"]};font-family:{_EDS_FONT};">'
    f'Analytics not connected</div>'
    f'<div style="font-size:13px;color:{_EDS["subhead"]};font-family:{_EDS_FONT};">'
    f'Set <code>DB_ENABLED=true</code> and configure '
    f'<code>DB_HOST</code> / <code>DB_NAME</code> / <code>DB_USER</code> / '
    f'<code>DB_PASSWORD</code> in <code>.env</code> to enable the PostgreSQL '
    f'analytics dashboard.</div></div>'
)


def run_analytics_ui():
    """Refresh handler for Tab 4 — queries Postgres and returns all chart data."""
    _empty = (None, None, None, None, None)

    # Guard 1: analytics disabled via env flag — return immediately, no network call
    if not settings.db_enabled:
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

        return (
            _analytics_kpi_html(stats),
            _severity_fig(sev),
            _status_fig(statuses),
            _rules_fig(rules),
            _owners_fig(owners),
            _timeline_fig(timeline),
            "",
        )
    except Exception as exc:
        logger.warning(f"Analytics DB error: {exc}")
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


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="AI-Readiness Dashboard") as demo:

        gr.Markdown(
            "# AI-Readiness Dashboard\n"
            "**Auditor · LangGraph · Reflexion · HITL · RAG · Mem0**"
        )

        # ── Shared state (accessible from all tabs) ───────────────────────────
        audit_state = gr.State(None)   # full audit result dict
        docs_state  = gr.State([])     # per-document list sorted by trust_score

        with gr.Tabs():

            # ═══════════════════════════════════════════════════════════════════
            # TAB 1 — SCAN
            # ═══════════════════════════════════════════════════════════════════
            with gr.Tab("🔍 Scan"):

                with gr.Row():
                    folder_input = gr.Textbox(
                        value=DEFAULT_FOLDER,
                        label="Folder path",
                        placeholder="Absolute or relative path to your document repository",
                        scale=4,
                    )
                    run_btn = gr.Button("▶  Run Audit", variant="primary", scale=1, min_width=140)

                file_upload_scan = gr.File(
                    label="Or drag & drop your own files (PDF, DOCX, XLSX, PPTX, CSV, TXT, MD)",
                    file_count="multiple",
                    file_types=[".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".json"],
                )

                extrapolate_cb = gr.Checkbox(
                    label=f"Extrapolate to full repository (×{EXTRAPOLATION_FACTOR:,}) — scales "
                          f"document count & remediation hours for illustration only",
                    value=False,
                )

                gr.Markdown("---")

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

                with gr.Row():
                    analytics_refresh_btn = gr.Button("🔄 Refresh", variant="primary", scale=1, min_width=120)
                    analytics_status      = gr.Markdown(value="")

                analytics_kpi = gr.HTML(value="")

                with gr.Row():
                    analytics_severity_plot = gr.Plot(label="")
                    analytics_status_plot   = gr.Plot(label="")

                with gr.Row():
                    analytics_rules_plot  = gr.Plot(label="")
                    analytics_owners_plot = gr.Plot(label="")

                analytics_timeline_plot = gr.Plot(label="")

        # ── Wiring ────────────────────────────────────────────────────────────

        # Tab 1 — Run Audit
        run_btn.click(
            run_audit_ui,
            inputs=[folder_input, file_upload_scan, extrapolate_cb],
            outputs=[
                audit_state, docs_state,
                hero_html, stats_html,
                reasons_plot, doc_type_plot,
                doc_table, detail_md,
            ],
        )

        # Tab 1 — extrapolation toggle re-renders hero + stats without re-running audit
        extrapolate_cb.change(
            update_extrapolation,
            inputs=[extrapolate_cb, audit_state],
            outputs=[hero_html, stats_html],
        )

        # Tab 2 — row selection → detail panel
        doc_table.select(
            select_doc_row,
            inputs=[docs_state],
            outputs=[detail_md],
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

        # Tab 4 — Analytics
        _analytics_outputs = [
            analytics_kpi,
            analytics_severity_plot, analytics_status_plot,
            analytics_rules_plot,    analytics_owners_plot,
            analytics_timeline_plot,
            analytics_status,
        ]
        analytics_refresh_btn.click(run_analytics_ui, outputs=_analytics_outputs)
        analytics_tab.select(run_analytics_ui,         outputs=_analytics_outputs)

        # Queue is required so slow/blocking handlers (analytics DB, chat streaming)
        # run in worker threads and cannot freeze the Gradio event loop.
        demo.queue()

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860)
