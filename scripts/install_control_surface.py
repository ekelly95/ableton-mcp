"""Install (or update) the AbletonMCP control surface into Ableton Live.

Thin wrapper kept for existing docs; the logic lives in mcp_server/install.py
so installed packages and MCP Bundles can use it too (`ableton-mcp-install`,
or the install_control_surface tool).

Run:  python scripts/install_control_surface.py              # and hot-reload Live
      python scripts/install_control_surface.py --no-reload
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.install import (  # noqa: E402,F401 - re-exported for tests
    candidate_macos_app_script_dirs,
    candidate_user_libraries,
    main,
)

if __name__ == "__main__":
    main()
