"""AbletonMCP control surface — entry point.

Runs inside Ableton Live as a MIDI Remote Script. Exposes Live's API over a
localhost socket (TCP 127.0.0.1:9877 on Windows) using length-prefixed JSON.

Install: copy this folder as "AbletonMCP" into Live's Remote Scripts location
(scripts/install_control_surface.py does this), restart Live, then pick
"AbletonMCP" under Preferences → Link/Tempo/MIDI → Control Surface.
"""

from .config import CONTROL_SURFACE_NAME, VERSION

__version__ = VERSION
__all__ = ["AbletonMCP", "create_instance"]

# _Framework exists only inside Live; the fallback keeps this package
# importable for tests and for the MCP server's registry import.
try:
    from _Framework.ControlSurface import ControlSurface

    _LIVE_ENVIRONMENT = True
except ImportError:
    _LIVE_ENVIRONMENT = False

    class ControlSurface:
        def __init__(self, c_instance):
            self._c_instance = c_instance

        def song(self):
            return None

        def application(self):
            return None

        def schedule_message(self, delay, callback):
            callback()

        def show_message(self, message):
            print(f"[Live Message] {message}")

        def disconnect(self):
            pass


from .log import get_logger
from .socket_server import SocketServer

# Importing the commands package registers every command with REGISTRY.
from . import commands

logger = get_logger("__init__")


class AbletonMCP(ControlSurface):
    def __init__(self, c_instance):
        super().__init__(c_instance)
        self._server = None
        try:
            self._server = SocketServer(self)
            self._server.start()
            self.show_message(f"{CONTROL_SURFACE_NAME} v{VERSION} ready")
            logger.info(f"{CONTROL_SURFACE_NAME} v{VERSION} initialized")
        except OSError as e:
            # Most likely: port already bound because the script was added
            # twice in Preferences, or another instance of Live runs it.
            logger.error(f"Socket bind failed: {e}")
            self.show_message(
                f"{CONTROL_SURFACE_NAME} FAILED: port in use? Remove duplicate entries "
                f"in Preferences > Link/Tempo/MIDI. ({e})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            self.show_message(f"{CONTROL_SURFACE_NAME} failed to start: {e}")
            raise

    def disconnect(self):
        logger.info("Disconnecting...")
        if self._server:
            try:
                self._server.stop()
            except Exception as e:
                logger.error(f"Error stopping server: {e}")
            self._server = None
        super().disconnect()
        logger.info("Disconnected")

    # Deliberately NOT overriding update_display/refresh_state/build_midi_map:
    # _Framework pumps scheduled messages inside update_display, so an empty
    # override silently kills thread marshaling (found at the P4 checkpoint —
    # every marshaled command timed out while ping still answered).


def create_instance(c_instance):
    """Factory Live calls to instantiate the control surface."""
    return AbletonMCP(c_instance)
