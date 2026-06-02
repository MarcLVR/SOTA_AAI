"""
CLI entry-point and programmatic API for the AI-Readiness Auditor.

Usage:
    python -m domain.run_audit <folder_path>
    python -m domain.run_audit <folder_path> --json        # machine-readable output
    python -m domain.run_audit <folder_path> --top 20      # show top N offenders
"""
from __future__ import annotations

import argparse
import json
import sys

from domain.tools.crawler import crawl_repository
from domain.tools.extractor import extract_document
from domain.tools.staleness import score_staleness
from domain.tools.standards import check_standards
from domain.tools.governance import check_governance
from domain.tools.aggregate import aggregate_findings, compute_trust_score


def audit_repository(folder: str, verbose: bool = False) -> dict:
    """Run the full audit pipeline on a folder and return the dashboard payload.

    Pipeline:
        crawl → extract → (staleness + standards + governance) → trust_score → aggregate
    """
    # ── 1. Crawl ──────────────────────────────────────────────────────────────
    if verbose:
        print(f"[1/3] Crawling {folder!r} …", flush=True)
    crawl = crawl_repository(folder)
    files = crawl["files"]

    if not files:
        print("No files found.", file=sys.stderr)
        return aggregate_findings([])

    if verbose:
        print(
            f"      Found {crawl['total_files']} files "
            f"({crawl['supported_files']} supported, "
            f"{crawl['unsupported_files']} unsupported) "
            f"— {_human_bytes(crawl['total_bytes'])}",
            flush=True,
        )

    # ── 2. Per-document analysis ──────────────────────────────────────────────
    if verbose:
        print(f"[2/3] Analysing {len(files)} files …", flush=True)

    document_results = []
    for i, file_record in enumerate(files, 1):
        path = file_record["path"]
        doc_type = file_record["doc_type"]
        extension = file_record["extension"]
        modified_at = file_record["modified_at"]
        accessed_at = file_record.get("accessed_at", "")

        if verbose:
            print(f"      [{i}/{len(files)}] {path}", flush=True)

        # Extract
        extracted = extract_document(path)
        text = extracted.get("text", "")
        embedded_metadata = extracted.get("embedded_metadata", {})
        extraction_ok = extracted.get("extraction_ok", True)

        # Three signals
        staleness  = score_staleness(doc_type, modified_at, accessed_at or None, content_text=text)
        standards  = check_standards(doc_type, extension, text)
        governance = check_governance(doc_type, embedded_metadata, text)

        # Trust score
        trust = compute_trust_score(
            staleness_score=staleness["staleness_score"],
            standards_score=standards["standards_score"],
            governance_score=governance["governance_score"],
        )

        document_results.append({
            "path":              path,
            "name":              file_record["name"],
            "doc_type":          doc_type,
            "extension":         extension,
            "size_bytes":        file_record["size_bytes"],
            "modified_at":       modified_at,
            "extraction_ok":     extraction_ok,
            "embedded_metadata": embedded_metadata,
            "staleness":         staleness,
            "standards":         standards,
            "governance":        governance,
            "trust_score":       trust["trust_score"],
            "needs_supervision":  trust["needs_supervision"],
        })

    # ── 3. Aggregate ──────────────────────────────────────────────────────────
    if verbose:
        print("[3/3] Aggregating …", flush=True)
    dashboard = aggregate_findings(document_results)
    # Attach raw per-document list so UIs can drill in without re-running the pipeline
    dashboard["documents"] = document_results
    return dashboard


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def _print_report(report: dict, top: int = 10) -> None:
    h = report["headline"]
    print("\n" + "=" * 60)
    print("  AI-READINESS AUDIT REPORT")
    print("=" * 60)
    print(f"  Documents scanned  : {h['total_documents']}")
    print(f"  Total size         : {h['total_size_human']}")
    print(f"  Needs supervision  : {h['needs_supervision_count']} "
          f"({h['needs_supervision_pct']:.1f}%)")

    est = report.get("estimated_remediation_hours", 0)
    print(f"  Est. remediation   : {est:.1f} hours")

    print("\n── Staleness ──────────────────────────────────────────────")
    s = report.get("staleness", {})
    print(f"  Stale docs  : {s.get('stale_count', 0)}")
    print(f"  Cold docs   : {s.get('cold_count', 0)}")
    if s.get("oldest_doc"):
        o = s["oldest_doc"]
        print(f"  Oldest      : {o['path']}  ({o['age_days']} days)")

    print("\n── Standards ──────────────────────────────────────────────")
    st = report.get("standards", {})
    print(f"  Non-std format  : {st.get('non_standard_format_count', 0)}")
    print(f"  Missing sections: {st.get('missing_sections_count', 0)}")
    print(f"  Retired std hits: {st.get('retired_standard_hits', 0)}")

    print("\n── Governance ─────────────────────────────────────────────")
    g = report.get("governance", {})
    print(f"  No owner       : {g.get('no_owner_count', 0)}")
    print(f"  Unclassified   : {g.get('unclassified_count', 0)}")

    print("\n── By document type ───────────────────────────────────────")
    for dt, counts in sorted(report.get("by_doc_type", {}).items()):
        print(f"  {dt:<20} total={counts['count']}  flagged={counts['flagged']}")

    offenders = report.get("top_offenders", [])[:top]
    if offenders:
        print(f"\n── Top {len(offenders)} offenders (lowest trust score) ───────────────")
        for rank, doc in enumerate(offenders, 1):
            print(f"  {rank:2}. [{doc['trust_score']:.2f}] {doc['path']}")
            print(f"      {doc['top_reason']}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-Readiness Auditor")
    parser.add_argument("folder", help="Path to the document repository to audit")
    parser.add_argument(
        "--json", nargs="?", const="-", default=None, metavar="FILE",
        help="Output JSON to FILE, or stdout when FILE is omitted",
    )
    parser.add_argument("--top", type=int, default=10, help="Number of top offenders to show")
    args = parser.parse_args()

    json_mode = args.json is not None
    report = audit_repository(args.folder, verbose=not json_mode)

    if json_mode:
        output = json.dumps(report, indent=2)
        if args.json == "-":
            print(output)
        else:
            from pathlib import Path as _Path
            _Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            _Path(args.json).write_text(output, encoding="utf-8")
            print(f"JSON written → {args.json}")
    else:
        _print_report(report, top=args.top)


if __name__ == "__main__":
    main()
