# packaging

- `build_mcpb.py` builds `dist/ableton-mcp-<version>.mcpb`, a one-click MCP
  Bundle (run: `uv run python packaging/build_mcpb.py`).
- `mcpb-manifest-v0.4.schema.json` is the official MCP Bundle manifest schema,
  vendored unchanged from https://github.com/modelcontextprotocol/mcpb
  (`schemas/mcpb-manifest-v0.4.schema.json`, MIT licence), so the build can
  validate its manifest offline.
