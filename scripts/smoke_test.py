"""Quick liveness check against the control surface running inside Live.

Run:  python scripts/smoke_test.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.client import AbletonClient, AbletonConnectionError, CommandError  # noqa: E402


def main() -> None:
    client = AbletonClient(timeout=10.0)
    try:
        result = client.send("ping")
    except (AbletonConnectionError, CommandError) as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        client.close()

    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and result.get("pong"):
        print(
            f"\nOK: AbletonMCP v{result.get('version')} responding, "
            f"{result.get('command_count')} commands registered."
        )
    else:
        print("\nFAIL: unexpected response")
        sys.exit(1)


if __name__ == "__main__":
    main()
