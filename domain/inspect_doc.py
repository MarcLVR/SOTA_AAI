"""
domain/inspect_doc.py — single-document auditor inspection tool.

Usage:
    python -m domain.inspect_doc <file_path>
    python -m domain.inspect_doc <file_path> --no-text   # skip extracted text section
    python -m domain.inspect_doc <file_path> --json      # machine-readable output

Prints a rich terminal report so you can judge whether findings are specific
and useful enough before a demo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── ANSI helpers ──────────────────────────────────────────────────────────────

_IS_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _IS_TTY else text

def red(t):    return _c("91", t)
def yellow(t): return _c("93", t)
def green(t):  return _c("92", t)
def cyan(t):   return _c("96", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)


# ── Severity colouring ────────────────────────────────────────────────────────

def _sev_label(sev: str) -> str:
    if sev == "high":   return red(bold("HIGH  "))
    if sev == "medium": return yellow(bold("MED   "))
    return green(bold("LOW   "))


def _score_bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    if score < 0.50:  colour = red
    elif score < 0.70: colour = yellow
    else:              colour = green
    bar = colour("█" * filled) + dim("░" * (width - filled))
    return f"[{bar}] {score:.3f}"


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def _days_ago(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        days = (datetime.now(tz=timezone.utc) - dt).days
        return f"{days:,} days ago"
    except Exception:
        return "unknown"


def _sep(char: str = "─", width: int = 68) -> str:
    return dim(char * width)


# ── Section printer ───────────────────────────────────────────────────────────

def _header(title: str) -> None:
    print()
    print(cyan(bold(f"  {title}")))
    print(f"  {_sep()}")


def _row(label: str, value: str, width: int = 16) -> None:
    print(f"  {dim(label.ljust(width))} {value}")


# ── Main report ───────────────────────────────────────────────────────────────

def inspect(file_path: str, show_text: bool = True, as_json: bool = False) -> None:
    path = Path(file_path).resolve()
    if not path.exists():
        print(red(f"File not found: {file_path}"), file=sys.stderr)
        sys.exit(1)

    # ── Import domain tools ───────────────────────────────────────────────────
    sys.path.insert(0, str(path.parent.parent))  # ensure repo root on path
    from domain.tools.crawler import crawl_repository
    from domain.tools.extractor import extract_document
    from domain.tools.staleness import score_staleness
    from domain.tools.standards import check_standards
    from domain.tools.governance import check_governance
    from domain.tools.aggregate import compute_trust_score
    from domain.knowledge import doc_type_from_filename
    from domain.adapter import audit_doc_to_findings

    # ── Stat ──────────────────────────────────────────────────────────────────
    stat = os.stat(path)
    size_bytes  = stat.st_size
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    accessed_at = datetime.fromtimestamp(stat.st_atime, tz=timezone.utc).isoformat()
    doc_type    = doc_type_from_filename(path.name)
    extension   = path.suffix.lower()

    # ── Extract ───────────────────────────────────────────────────────────────
    extracted       = extract_document(str(path))
    text            = extracted.get("text", "")
    embedded_meta   = extracted.get("embedded_metadata", {})
    extraction_ok   = extracted.get("extraction_ok", True)

    # ── Try connector for richer section/table info ───────────────────────────
    connector_result = None
    try:
        from agents.connector import load_document as _load
        connector_result = _load(str(path))
    except Exception:
        pass  # connector is optional; domain tools are the authority

    # ── Score ─────────────────────────────────────────────────────────────────
    staleness  = score_staleness(doc_type, modified_at, accessed_at, content_text=text)
    standards  = check_standards(doc_type, extension, text)
    governance = check_governance(doc_type, embedded_meta, text)
    trust      = compute_trust_score(
        staleness_score=staleness["staleness_score"],
        standards_score=standards["standards_score"],
        governance_score=governance["governance_score"],
    )

    # ── Adapter ───────────────────────────────────────────────────────────────
    doc_result = {
        "path":              str(path),
        "name":              path.name,
        "doc_type":          doc_type,
        "extension":         extension,
        "size_bytes":        size_bytes,
        "modified_at":       modified_at,
        "extraction_ok":     extraction_ok,
        "embedded_metadata": embedded_meta,
        "staleness":         staleness,
        "standards":         standards,
        "governance":        governance,
        "trust_score":       trust["trust_score"],
        "needs_supervision": trust["needs_supervision"],
    }
    findings = audit_doc_to_findings(doc_result)

    # ── JSON output ───────────────────────────────────────────────────────────
    if as_json:
        output = {
            "path":         str(path),
            "name":         path.name,
            "doc_type":     doc_type,
            "size_bytes":   size_bytes,
            "modified_at":  modified_at,
            "extraction_ok": extraction_ok,
            "embedded_metadata": embedded_meta,
            "trust_score":  trust["trust_score"],
            "needs_supervision": trust["needs_supervision"],
            "staleness":    staleness,
            "standards":    standards,
            "governance":   governance,
            "findings":     findings,
            "text_length":  len(text),
            "text_preview": text[:500],
        }
        print(json.dumps(output, indent=2, default=str))
        return

    # ── Human-readable report ─────────────────────────────────────────────────

    print()
    print(bold("  " + "═" * 68))
    print(bold(f"  {path.name}"))
    print(bold("  " + "═" * 68))

    # ── 1. Document ───────────────────────────────────────────────────────────
    _header("DOCUMENT")
    _row("Name",     path.name)
    _row("Type",     doc_type)
    _row("Extension",extension)
    _row("Size",     _human_bytes(size_bytes))
    _row("Modified", f"{modified_at[:10]}  {dim('(' + _days_ago(modified_at) + ')')}")
    _row("Accessed", f"{accessed_at[:10]}  {dim('(' + _days_ago(accessed_at) + ')')}")
    _row("Path",     dim(str(path)))

    # ── 2. Extraction ─────────────────────────────────────────────────────────
    _header("EXTRACTION")
    ok_str = green("✓ OK") if extraction_ok else red("✗ FAILED")
    _row("Status",     ok_str)
    _row("Characters", f"{len(text):,}")
    if embedded_meta:
        for k, v in embedded_meta.items():
            _row(k.capitalize(), str(v)[:80] if v else dim("—"))
    else:
        _row("Metadata", dim("none embedded"))

    # Connector sections/tables if available
    if connector_result:
        secs = connector_result.get("sections", [])
        tbls = connector_result.get("tables", [])
        imgs = connector_result.get("images_count", 0)
        _row("Sections", str(len(secs)))
        _row("Tables",   str(len(tbls)))
        _row("Images",   str(imgs))
        if secs:
            headings = [s["heading"] for s in secs if s.get("heading")][:6]
            if headings:
                _row("Headings", ", ".join(headings))

    # ── 3. Trust score ────────────────────────────────────────────────────────
    _header("TRUST SCORE")
    ts = trust["trust_score"]
    verdict = red(bold("NEEDS SUPERVISION  ⚠")) if trust["needs_supervision"] else green(bold("PASSES THRESHOLD  ✓"))
    print(f"  {bold(_score_bar(ts, 24))}  {verdict}")
    print()
    print(f"  {dim('Staleness: ')} {_score_bar(staleness['staleness_score'], 12)}  "
          f"(weight 40%)")
    print(f"  {dim('Standards: ')} {_score_bar(standards['standards_score'], 12)}  "
          f"(weight 35%)")
    print(f"  {dim('Governance:')} {_score_bar(governance['governance_score'], 12)}  "
          f"(weight 25%)")

    # ── 4. Staleness ─────────────────────────────────────────────────────────
    _header(f"STALENESS  (score: {staleness['staleness_score']:.3f})")
    age   = staleness["age_days"]
    thresh = staleness["threshold_days"]
    stale_str = red("STALE ✗") if staleness["is_stale"] else green("fresh ✓")
    cold_str  = yellow("COLD !")  if staleness["is_cold"]  else green("accessed ✓")
    _row("Age / threshold", f"{age:,} days / {thresh:,} days")
    _row("Stale",   stale_str)
    _row("Cold",    cold_str)
    if staleness["findings"]:
        print(f"  {dim('Findings:')}")
        for f in staleness["findings"]:
            print(f"    • {f}")
    else:
        print(f"  {green(dim('No staleness findings'))}")

    # ── 5. Standards ─────────────────────────────────────────────────────────
    _header(f"STANDARDS  (score: {standards['standards_score']:.3f})")
    fmt_str = green("✓ standard") if standards["is_standard_format"] else red("✗ non-standard")
    _row("Format", fmt_str)
    req  = standards.get("required_sections",  [])
    pres = standards.get("present_sections",   [])
    miss = standards.get("missing_sections",   [])
    _row("Sections", f"{len(pres)}/{len(req)} present" if req else dim("none required"))
    if miss:
        _row("Missing", red(", ".join(miss)))
    ret = standards.get("retired_standard_hits", [])
    if ret:
        _row("Retired refs", red(f"{len(ret)} hit(s): " + ", ".join(
            h.get("trigger", str(h)) if isinstance(h, dict) else str(h)
            for h in ret
        )[:80]))
    ph = standards.get("placeholder_hits", [])
    if ph:
        _row("Placeholders", yellow(f"{len(ph)}: " + ", ".join(str(p) for p in ph)[:60]))
    if standards["findings"]:
        print(f"  {dim('Findings:')}")
        for f in standards["findings"]:
            print(f"    • {f}")
    else:
        print(f"  {green(dim('No standards findings'))}")

    # ── 6. Governance ─────────────────────────────────────────────────────────
    _header(f"GOVERNANCE  (score: {governance['governance_score']:.3f})")
    owner_str = green("✓ has owner")  if governance["has_owner"]           else red("✗ no owner")
    cls_str   = green("✓ classified") if governance["classification_valid"] else red("✗ unclassified")
    _row("Owner",          owner_str)
    _row("Classification", f"{cls_str}  {dim(governance.get('classification','') or '')}")
    req_fields  = governance.get("required_fields",  [])
    pres_fields = governance.get("present_fields",   [])
    miss_fields = governance.get("missing_fields",   [])
    _row("Fields",  f"{len(pres_fields)}/{len(req_fields)} present" if req_fields else dim("none required"))
    if miss_fields:
        _row("Missing", red(", ".join(miss_fields)))
    if governance["findings"]:
        print(f"  {dim('Findings:')}")
        for f in governance["findings"]:
            print(f"    • {f}")
    else:
        print(f"  {green(dim('No governance findings'))}")

    # ── 7. Adapter output (chase format) ──────────────────────────────────────
    _header(f"ADAPTER OUTPUT  ({len(findings)} findings → chase/notifier/db format)")
    if not findings:
        print(f"  {green('No findings — document passes all checks.')}")
    else:
        for i, f in enumerate(findings, 1):
            sev   = f["severity"]
            print()
            print(f"  {bold(str(i) + '.')} {_sev_label(sev)} {bold(f['title'])}")
            print(f"     {dim('rule_code:')} {f['rule_code']}")
            print(f"     {dim('location: ')} {f['location']}")
            print(f"     {dim('detail:   ')} {f['detail'][:100]}")
            print(f"     {dim('fix:      ')} {f['suggestion'][:100]}")

    # ── 8. Extracted text preview ─────────────────────────────────────────────
    if show_text:
        _header(f"EXTRACTED TEXT  (first 800 chars of {len(text):,})")
        preview = text[:800].strip()
        if preview:
            for line in preview.split("\n"):
                print(f"  {dim(line)}")
            if len(text) > 800:
                print(f"  {dim('…')}")
        else:
            print(f"  {dim('(no text extracted)')}")

    print()
    print("  " + _sep("═"))
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a single document through the audit pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m domain.inspect_doc domain/demo_corpus/files/vendor_nda_template_2019.docx
  python -m domain.inspect_doc contracts/nda_2019.docx --no-text
  python -m domain.inspect_doc report.pdf --json | jq .findings
        """,
    )
    parser.add_argument("file", help="Path to the document to inspect")
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="Skip the extracted text preview section",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of the terminal report",
    )
    args = parser.parse_args()
    inspect(args.file, show_text=not args.no_text, as_json=args.json)


if __name__ == "__main__":
    main()
