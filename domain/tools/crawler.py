"""
Repository crawler — inventories a folder and collects filesystem-level metadata.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from domain.knowledge import doc_type_from_filename


@dataclass
class FileRecord:
    """One inventoried item. The unit the rest of the pipeline operates on."""
    path: str
    name: str
    extension: str
    size_bytes: int
    modified_at: str
    accessed_at: str
    created_at: str
    doc_type: str = "unknown"
    findings: list = field(default_factory=list)


SUPPORTED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".csv", ".json", ".txt", ".md",
}

# ── Junk-file filters (SharePoint / OneDrive artifacts) ──────────────────────

_SKIP_DIRS: set[str] = {
    ".git", ".venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".tox", "dist", "build", ".eggs",
    # OneDrive internals — never contain real documents
    "OneDriveTemp", "OneDriveCloudTemp",
}

# Exact filenames to skip (lowercased for comparison)
_SKIP_FILES: frozenset[str] = frozenset({
    "desktop.ini", "thumbs.db", "thumbs.db:encryptable",
    "ehthumbs.db", "ehthumbs_vista.db", ".ds_store",
})

# Filename prefixes that indicate temp / lock files
_JUNK_PREFIXES: tuple[str, ...] = ("~$", "~")

# Filename suffixes that indicate temp / incomplete files (lowercased)
_JUNK_SUFFIXES: tuple[str, ...] = (".tmp", ".crdownload", ".part", ".lock", ".lnk")


def crawl_repository(folder_path: str, max_files: int | None = None) -> dict:
    """Walk a folder recursively and inventory every file with its metadata.

    Skips SharePoint/OneDrive junk files (desktop.ini, ~$ lock files, .tmp, etc.).
    Unreadable or cloud-only placeholder files are collected separately instead of
    silently dropped, so the caller can report them.

    Args:
        folder_path: Root directory to crawl.
        max_files:   If set, stop cataloguing supported files once this many are
                     found (unsupported + unreadable files still counted for the
                     headline). None means no limit.
    """
    files: list[dict] = []
    unreadable_files: list[dict] = []
    total_bytes = 0
    by_extension: dict[str, int] = {}
    supported_files = 0
    unsupported_files = 0

    for root, dirs, filenames in os.walk(folder_path):
        # Prune noise dirs in-place so os.walk skips them entirely
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            name_lower = filename.lower()

            # ── Skip hidden files ─────────────────────────────────────────────
            if filename.startswith("."):
                continue

            # ── Skip SharePoint/OneDrive junk ─────────────────────────────────
            if name_lower in _SKIP_FILES:
                continue
            if any(filename.startswith(p) for p in _JUNK_PREFIXES):
                continue  # Office lock files (~$document.docx)
            if any(name_lower.endswith(s) for s in _JUNK_SUFFIXES):
                continue

            full_path = os.path.join(root, filename)
            ext = Path(filename).suffix.lower()

            # ── Handle unreadable / locked / cloud-only files ─────────────────
            try:
                stat = os.stat(full_path)
            except OSError as exc:
                unreadable_files.append({
                    "path":  full_path,
                    "name":  filename,
                    "error": f"Could not stat: {exc}",
                })
                continue

            size = stat.st_size

            # Zero-byte supported files are almost always OneDrive cloud-only
            # placeholders ("Files On-Demand" not yet downloaded).
            if size == 0 and ext in SUPPORTED_EXTENSIONS:
                unreadable_files.append({
                    "path":  full_path,
                    "name":  filename,
                    "error": (
                        "Zero-byte file — may be an OneDrive cloud-only placeholder "
                        "(file not downloaded). Open it in Windows to sync it first."
                    ),
                })
                continue

            record = FileRecord(
                path=full_path,
                name=filename,
                extension=ext,
                size_bytes=size,
                modified_at=_iso(stat.st_mtime),
                accessed_at=_iso(stat.st_atime),
                created_at=_iso(stat.st_ctime),
                doc_type=doc_type_from_filename(filename),
            )

            files.append(asdict(record))
            total_bytes += size
            by_extension[ext] = by_extension.get(ext, 0) + 1

            if ext in SUPPORTED_EXTENSIONS:
                supported_files += 1
                if max_files and supported_files >= max_files:
                    # Soft cap reached — stop adding but finish this directory
                    break
            else:
                unsupported_files += 1

    return {
        "files":             files,
        "total_files":       len(files),
        "total_bytes":       total_bytes,
        "by_extension":      by_extension,
        "supported_files":   supported_files,
        "unsupported_files": unsupported_files,
        "unreadable_files":  unreadable_files,
        "unreadable_count":  len(unreadable_files),
    }


def _iso(ts: float) -> str:
    """Epoch seconds → ISO 8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
