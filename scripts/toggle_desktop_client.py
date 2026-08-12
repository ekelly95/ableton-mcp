"""Add or remove this server from Claude Desktop's MCP config.

Live's bridge serves ONE client at a time and holds the connection open once
made, so Claude Desktop and Codex cannot both drive Ableton in the same sitting
— whichever asks second hangs. This flips Desktop's registration on and off so
switching between the two is one command instead of hand-editing JSON.

The entry is rebuilt from the constant below rather than stashed in the config
file, so the way back survives Claude Desktop rewriting its own config.

Run:  python scripts/toggle_desktop_client.py          # show which way round it is
      python scripts/toggle_desktop_client.py on       # Desktop drives Ableton
      python scripts/toggle_desktop_client.py off      # Codex drives Ableton

Restart Claude Desktop after either change — it reads this file only at launch.
"""

import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "ableton"
if sys.platform == "darwin":
    CONFIG = (
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    )
    ENTRY = {"command": str(REPO_ROOT / ".venv" / "bin" / "ableton-mcp"), "args": []}
else:
    CONFIG = (
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "Claude"
        / "claude_desktop_config.json"
    )
    ENTRY = {
        "command": str(REPO_ROOT / ".venv" / "Scripts" / "ableton-mcp.exe"),
        "args": [],
    }


def load() -> dict:
    if not CONFIG.exists():
        sys.exit(f"No Claude Desktop config at {CONFIG}. Is Claude Desktop installed?")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save(config: dict) -> None:
    # Keep a copy of the last good version: this file also holds every other
    # MCP server registration, and a bad write would take them all with it.
    shutil.copy2(CONFIG, CONFIG.with_suffix(".json.bak"))
    CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    config = load()
    servers = config.setdefault("mcpServers", {})
    registered = SERVER_NAME in servers

    if action == "status":
        where = "Claude Desktop" if registered else "Codex only"
        print(
            f"{SERVER_NAME}: {'registered' if registered else 'not registered'} in Claude Desktop"
        )
        print(f"Ableton is currently reachable from: {where}")
        return

    if action == "on":
        if registered:
            print(f"{SERVER_NAME} is already registered in Claude Desktop. Nothing to do.")
            return
        if not Path(ENTRY["command"]).exists():
            sys.exit(f"{ENTRY['command']} is missing — run `uv pip install -e .` first.")
        servers[SERVER_NAME] = ENTRY
        save(config)
        print(f"Registered {SERVER_NAME} in Claude Desktop.")
        print("Restart Claude Desktop, and don't drive Ableton from Codex at the same time.")
        return

    if action == "off":
        if not registered:
            print(f"{SERVER_NAME} is not registered in Claude Desktop. Nothing to do.")
            return
        del servers[SERVER_NAME]
        save(config)
        print(f"Removed {SERVER_NAME} from Claude Desktop. Codex now has Ableton to itself.")
        print("Restart Claude Desktop to free the tools it is still holding.")
        return

    sys.exit(f"Unknown argument {action!r}. Use: on, off, or nothing for status.")


if __name__ == "__main__":
    main()
