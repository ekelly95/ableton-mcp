"""Configuration constants for the AbletonMCP control surface.

Single source of the project version: pyproject.toml must match VERSION
(enforced by tests/test_version_sync.py).
"""

import os
from pathlib import Path

VERSION: str = "2.0.0"
CONTROL_SURFACE_NAME: str = "AbletonMCP"

# IPC: TCP on Windows (no Unix sockets), Unix socket elsewhere
TCP_HOST: str = "127.0.0.1"
TCP_PORT: int = 9877
SOCKET_PATH: str = "/tmp/ableton_mcp.sock"
USE_TCP: bool = os.name == "nt"

# Wire framing: 4-byte big-endian length prefix + UTF-8 JSON payload
HEADER_SIZE: int = 4
MAX_MESSAGE_SIZE: int = 16 * 1024 * 1024

# Command execution timeouts (seconds)
DEFAULT_TIMEOUT_SECONDS: float = 30.0
COMMAND_TIMEOUTS: dict = {
    # Browser loads can trigger sample/pack indexing on first use
    "load_item": 120.0,
    "create_track": 60.0,
    "duplicate_track": 60.0,
    "delete_track": 60.0,
    "duplicate_clip": 60.0,
    "create_scene": 60.0,
    "add_notes": 60.0,
}

# Socket server behaviour
SOCKET_BACKLOG: int = 5
SOCKET_ACCEPT_TIMEOUT: float = 1.0
SOCKET_BUFFER_SIZE: int = 8192

# Logging
if os.name == "nt":
    LOG_DIR: Path = Path(os.environ.get("TEMP", "C:/Temp")) / "ableton_mcp_logs"
else:
    LOG_DIR: Path = Path("/tmp/ableton_mcp_logs")
LOG_FILE_MAX_SIZE: int = 10 * 1024 * 1024
LOG_BACKUP_COUNT: int = 5

# Payload bounds: reads run on Live's main thread, so responses must stay
# small enough not to stall the UI (see docs/architecture.md).
MAX_NOTES_PER_READ: int = 2000
MAX_BROWSER_ITEMS: int = 200
MAX_BROWSER_DEPTH: int = 4
