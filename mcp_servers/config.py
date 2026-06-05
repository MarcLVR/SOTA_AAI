from __future__ import annotations

import json
import logging

from config import settings

log = logging.getLogger(__name__)

# ── Local stdio server ────────────────────────────────────────────────────────

MCP_SERVERS: dict[str, dict] = {
    "filesystem": {
        "transport": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            str(settings.uploads_path.resolve()),
        ],
    },
}

# ── Remote HTTP servers (sse | streamable_http) ───────────────────────────────
# Configured via MCP_HTTP_SERVERS env var (JSON list).
# Each entry must have: name, transport, url
# Optional: headers (dict), timeout (seconds)
#
# Example .env entry:
#   MCP_HTTP_SERVERS=[{"name":"my-server","transport":"streamable_http","url":"https://example.com/mcp","headers":{"Authorization":"Bearer sk-..."}}]

def _load_http_servers() -> None:
    raw = settings.mcp_http_servers.strip()
    if not raw or raw == "[]":
        return
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("MCP_HTTP_SERVERS is not valid JSON — skipping: %s", exc)
        return

    for entry in entries:
        name = entry.get("name")
        transport = entry.get("transport")
        url = entry.get("url")

        if not name or not transport or not url:
            log.warning("MCP HTTP server entry missing name/transport/url — skipping: %s", entry)
            continue
        if transport not in ("sse", "streamable_http"):
            log.warning("Unsupported MCP transport %r for server %r — skipping", transport, name)
            continue

        cfg: dict = {"transport": transport, "url": url}
        if "headers" in entry:
            cfg["headers"] = entry["headers"]
        if "timeout" in entry:
            cfg["timeout"] = entry["timeout"]

        MCP_SERVERS[name] = cfg
        log.info("MCP HTTP server registered: %r (%s)", name, transport)


_load_http_servers()
