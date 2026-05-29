"""
File I/O tools: read, write, list files in the uploads sandbox.
Paths are constrained to the configured MCP_FILESYSTEM_ROOT.
"""
from pathlib import Path
from langchain_core.tools import tool
from config import settings
from loguru import logger


def _safe_path(filename: str) -> Path:
    """Resolve a filename inside the uploads root, blocking path traversal."""
    root = Path(settings.mcp_filesystem_root).resolve()
    target = (root / filename).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path traversal blocked: {filename}")
    return target


@tool
def read_file(filename: str) -> str:
    """
    Read a text file from the uploads directory and return its contents.

    Args:
        filename: Relative filename inside the uploads folder (e.g. 'notes.txt').
    """
    logger.info(f"[read_file] {filename}")
    try:
        path = _safe_path(filename)
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"File not found: {filename}"
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def write_file(filename: str, content: str) -> str:
    """
    Write content to a file in the uploads directory.

    Args:
        filename: Relative filename (e.g. 'output.md').
        content: Text content to write.
    """
    logger.info(f"[write_file] {filename} ({len(content)} chars)")
    try:
        path = _safe_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"File written: {filename} ({len(content)} characters)"
    except Exception as e:
        return f"Error writing file: {e}"


@tool
def list_files(subdir: str = "") -> str:
    """
    List files in the uploads directory (or a subdirectory).

    Args:
        subdir: Optional subdirectory within uploads (default: root).
    """
    logger.info(f"[list_files] subdir='{subdir}'")
    try:
        root = Path(settings.mcp_filesystem_root).resolve()
        target = (root / subdir).resolve() if subdir else root
        files = sorted(target.rglob("*"))
        items = [str(f.relative_to(root)) for f in files if f.is_file()]
        return "\n".join(items) if items else "No files found."
    except Exception as e:
        return f"Error listing files: {e}"
