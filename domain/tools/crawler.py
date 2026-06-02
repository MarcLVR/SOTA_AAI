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

_SKIP_DIRS: set[str] = {
    ".git", ".venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".tox", "dist", "build", ".eggs",
}


def crawl_repository(folder_path: str) -> dict:
    """Walk a folder recursively and inventory every file with its metadata."""
    files: list[dict] = []
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
            if filename.startswith("."):
                continue

            full_path = os.path.join(root, filename)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue

            ext = Path(filename).suffix.lower()
            size = stat.st_size

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
            else:
                unsupported_files += 1

    return {
        "files": files,
        "total_files": len(files),
        "total_bytes": total_bytes,
        "by_extension": by_extension,
        "supported_files": supported_files,
        "unsupported_files": unsupported_files,
    }


def _iso(ts: float) -> str:
    """Epoch seconds → ISO 8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
