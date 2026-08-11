"""The control surface package must import cleanly OUTSIDE Live.

The MCP server imports control_surface.registry (and the commands package,
which registers every command) to generate its tools. If anyone adds a
top-level `import Live` or unguarded `_Framework` import, that breaks —
this test catches it in a pristine interpreter.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_registry_importable_without_live():
    code = (
        "import sys; "
        "assert 'Live' not in sys.modules; "
        "import control_surface; "
        "import control_surface.commands; "
        "from control_surface.registry import REGISTRY; "
        "print('ok', len(REGISTRY))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "ok" in result.stdout
