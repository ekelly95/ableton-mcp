"""ableton-mcp server package. `run()` is the console entry point."""


def run() -> None:
    import anyio
    from mcp.server.stdio import stdio_server

    from .server import build_server, logger

    server = build_server()

    async def _amain() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    logger.info("ableton-mcp server starting (stdio)")
    try:
        anyio.run(_amain)
    except KeyboardInterrupt:
        logger.info("Server stopped")
