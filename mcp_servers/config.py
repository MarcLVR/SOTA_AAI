from config import settings

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
