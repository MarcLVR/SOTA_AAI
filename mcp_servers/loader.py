"""
Async loader for MCP servers using langchain-mcp-adapters.

Usage (inside an async context):
    async with load_mcp_tools(["filesystem", "fetch"]) as tools:
        # tools is a list of LangChain BaseTool instances
        agent = create_react_agent(llm, tools)
        ...

The context manager starts each MCP server subprocess, discovers its tools,
and shuts them down cleanly on exit.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, List

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import Callbacks, MultiServerMCPClient
from loguru import logger

from .config import MCP_SERVERS
from guardrails.elicitation import elicitation_callback

_CALLBACKS = Callbacks(on_elicitation=elicitation_callback)


@asynccontextmanager
async def load_mcp_tools(
    server_names: List[str] | None = None,
) -> AsyncIterator[List[BaseTool]]:
    """
    Async context manager that starts the requested MCP servers and yields
    their tools as a flat list of LangChain tools.

    Args:
        server_names: List of keys from MCP_SERVERS. Defaults to all servers.
    """
    names = server_names or list(MCP_SERVERS.keys())
    servers = {k: v for k, v in MCP_SERVERS.items() if k in names}

    if not servers:
        logger.warning("No MCP servers configured — yielding empty tool list")
        yield []
        return

    logger.info(f"Starting MCP servers: {list(servers.keys())}")

    # Load each server in isolation so one unreachable/misconfigured server
    # doesn't take down the whole tool set.
    clients: list[MultiServerMCPClient] = []
    tools: List[BaseTool] = []
    try:
        for name, cfg in servers.items():
            try:
                client = MultiServerMCPClient({name: cfg}, callbacks=_CALLBACKS)
                server_tools = await client.get_tools()
                clients.append(client)
                tools.extend(server_tools)
                logger.info(f"MCP '{name}': {len(server_tools)} tool(s)")
            except Exception as e:
                logger.error(f"MCP server '{name}' failed to load — skipping: {e}")
        logger.info(f"MCP tools loaded: {[t.name for t in tools]}")
        yield tools
    finally:
        logger.info("Shutting down MCP servers")
        for client in clients:
            try:
                await client.aclose()
            except Exception as e:
                logger.warning(f"MCP client close error: {e}")


def get_mcp_tools_sync(server_names: List[str] | None = None) -> List[BaseTool]:
    """
    Synchronous helper — runs the async loader in a new event loop.
    Use this when you cannot use async/await (e.g., Gradio callbacks).

    Each MCP tool opens a fresh session per call, so the loader closes its
    clients before returning — no leaked connections/subprocesses. Servers are
    loaded in isolation; a failing server is skipped, not fatal.
    """
    names = server_names or list(MCP_SERVERS.keys())
    servers = {k: v for k, v in MCP_SERVERS.items() if k in names}

    if not servers:
        return []

    async def _load() -> List[BaseTool]:
        tools: List[BaseTool] = []
        for name, cfg in servers.items():
            client = MultiServerMCPClient({name: cfg}, callbacks=_CALLBACKS)
            try:
                tools.extend(await client.get_tools())
            except Exception as e:
                logger.error(f"MCP server '{name}' failed to load (sync) — skipping: {e}")
            finally:
                try:
                    await client.aclose()
                except Exception as e:
                    logger.warning(f"MCP client close error: {e}")
        return tools

    tools = asyncio.run(_load())
    logger.info(f"MCP tools (sync) loaded: {[t.name for t in tools]}")
    return tools
