"""
MCP elicitation bridge — routes server elicitation requests to the Gradio UI.

When an MCP server sends an elicitation/create request mid-tool-call, the
async callback puts the request on the bus and blocks until the user responds
via the UI (or the request times out).

Two modes defined by the MCP spec:
  form  — server needs structured input (fields with types/descriptions)
  url   — server needs the user to visit an external URL (OAuth, payments…)

UI flow:
  1. gr.Timer polls get_pending() every 2 s
  2. Panel appears with the server's message + form fields or URL
  3. User fills in and clicks Submit (or Decline)
  4. submit_response() / decline() fires the threading.Event
  5. callback unblocks and returns ElicitResult to the MCP server
  6. Tool call completes normally
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from mcp import types

ELICITATION_TIMEOUT = 120  # seconds before auto-decline


@dataclass
class PendingElicitation:
    request_id: str
    mode: str                        # "form" | "url"
    message: str
    # form mode
    schema: dict[str, Any]           # JSON Schema properties dict
    # url mode
    url: str = ""
    # resolution
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    result: dict[str, Any] | None = None
    declined: bool = False


# ── Bus ───────────────────────────────────────────────────────────────────────

_pending: dict[str, PendingElicitation] = {}
_lock = threading.Lock()


def get_pending() -> list[PendingElicitation]:
    """Return all unresolved elicitation requests (for UI polling)."""
    with _lock:
        return list(_pending.values())


def submit_response(request_id: str, data: dict[str, Any]) -> bool:
    """Called by the UI when the user fills in the form and clicks Submit."""
    with _lock:
        req = _pending.get(request_id)
    if not req:
        return False
    req.result = data
    req._event.set()
    return True


def decline(request_id: str) -> bool:
    """Called by the UI when the user clicks Decline."""
    with _lock:
        req = _pending.get(request_id)
    if not req:
        return False
    req.declined = True
    req._event.set()
    return True


# ── Callback (wired into MultiServerMCPClient) ────────────────────────────────

async def elicitation_callback(
    mcp_context: Any,
    params: types.ElicitRequestParams,
    lc_context: Any,
) -> types.ElicitResult:
    """
    Async callback passed to langchain-mcp-adapters Callbacks(on_elicitation=...).

    Blocks the tool-call coroutine until the user responds in the UI or the
    request times out (ELICITATION_TIMEOUT seconds → auto-decline).
    """
    request_id = uuid.uuid4().hex[:8]

    if isinstance(params, types.ElicitRequestFormParams):
        req = PendingElicitation(
            request_id=request_id,
            mode="form",
            message=params.message,
            schema=params.requestedSchema or {},
        )
    else:
        req = PendingElicitation(
            request_id=request_id,
            mode="url",
            message=params.message,
            schema={},
            url=getattr(params, "url", ""),
        )

    with _lock:
        _pending[request_id] = req

    try:
        loop = asyncio.get_event_loop()
        signaled = await loop.run_in_executor(
            None, lambda: req._event.wait(ELICITATION_TIMEOUT)
        )

        if not signaled or req.declined:
            return types.ElicitResult(action="decline")

        return types.ElicitResult(
            action="accept",
            content=req.result if req.result is not None else {},
        )
    finally:
        with _lock:
            _pending.pop(request_id, None)
