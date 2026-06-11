"""
Sandboxed Python code executor.

Runs code in a SEPARATE subprocess, not in-process.
This means a crash, infinite loop, or sys.exit() in user code cannot
bring down the main agent process.

Safety properties:
  - Isolated process (full memory separation)
  - Hard wall-clock timeout (SIGKILL after N seconds)
  - stdout + stderr captured and returned
  - Temp file cleaned up after execution

For production: swap subprocess for E2B cloud sandboxes
  (pip install e2b-code-interpreter; one-line change in _run_subprocess).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from config import settings

_DEFAULT_TIMEOUT = 30  # seconds


def _run_subprocess(code: str, timeout: int = _DEFAULT_TIMEOUT) -> tuple[str, str]:
    """
    Write code to a temp file, execute it in a subprocess, return (stdout, stderr).
    The subprocess is killed unconditionally after `timeout` seconds.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            [sys.executable, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(settings.uploads_path),   # working dir = uploads sandbox
        )
        return result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return "", f"Execution timed out after {timeout} seconds."
    except Exception as e:
        return "", f"Subprocess launch failed: {e}"
    finally:
        tmp_path.unlink(missing_ok=True)


@tool
def python_repl(code: str, timeout: int = 30) -> str:
    """
    Execute Python code in an isolated subprocess and return the output.
    Use this for calculations, data analysis, or verifying logic.
    The working directory is the uploads folder — files written here persist.

    Args:
        code: Valid Python code to execute.
        timeout: Maximum execution time in seconds (default 30).
    """
    logger.info(f"[python_repl] executing ({len(code)} chars, timeout={timeout}s)")

    # Only set a non-interactive matplotlib backend when the snippet actually uses
    # matplotlib, and never let a missing install break execution. Unconditionally
    # importing matplotlib would make *every* call fail when it isn't installed.
    if "matplotlib" in code or "pyplot" in code or "plt" in code:
        preamble = textwrap.dedent("""
            try:
                import matplotlib
                matplotlib.use('Agg')
            except ImportError:
                pass
        """)
        full_code = preamble + "\n" + code
    else:
        full_code = code

    stdout, stderr = _run_subprocess(full_code, timeout=timeout)

    if stderr and not stdout:
        return f"ERROR:\n{stderr.strip()}"
    if stderr:
        return f"OUTPUT:\n{stdout.strip()}\n\nSTDERR (warnings):\n{stderr.strip()}"
    return stdout.strip() if stdout.strip() else "Code executed successfully (no output)."
