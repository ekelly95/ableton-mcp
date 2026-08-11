"""Max for Live tap client + audio-levels tool.

TapClient deliberately does NOT subclass AbletonClient: that client's resend
logic consults the command REGISTRY (tap commands aren't in it) and its
timeouts come from bridge config. The ~60 framing lines are copied instead —
two tiny clients beat one coupled one.
"""

import json
import logging
import socket
import struct
import threading
import time
import uuid
from typing import Any

import mcp.types as types

HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024
TAP_HOST = "127.0.0.1"
TAP_PORT = 9878
CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 5.0
EXPECTED_TAP_PROTOCOL = 2

logger = logging.getLogger("ableton-mcp.m4l")

UNAVAILABLE_HINT = (
    "Audio tap not detected. Drop the 'AbletonMCP Tap' Max Audio Effect on the "
    "Master track (requires Live Suite or Max for Live) — build steps in "
    "m4l/README-lab.md. Then ensure the device is powered on and Live's audio "
    "engine is running."
)

REBUILD_HINT = (
    "Tap device is out of date: the device in Live speaks an older measurement "
    "protocol than this server (or its patch predates the current "
    "tap_server.js). Rebuild it once from the current m4l/tap.maxpat AND copy "
    "the current m4l/tap_server.js beside the saved device (or re-freeze) — "
    "steps in m4l/README-lab.md, 'Upgrading from v1'. Levels are withheld "
    "rather than serving misleading numbers."
)


class TapUnavailable(Exception):
    pass


class TapClient:
    """Minimal one-shot client for the tap's length-prefixed JSON protocol."""

    def __init__(self, host: str = TAP_HOST, port: int = TAP_PORT):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._warned_version = False

    def send(self, command: str, **params: Any) -> dict[str, Any]:
        request = {"type": command, "params": params, "id": str(uuid.uuid4())}
        body = json.dumps(request).encode("utf-8")
        with self._lock:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
            except OSError as e:
                raise TapUnavailable(str(e)) from e
            try:
                sock.settimeout(READ_TIMEOUT)
                sock.sendall(struct.pack(">I", len(body)) + body)
                header = self._recv_exact(sock, HEADER_SIZE)
                length = struct.unpack(">I", header)[0]
                if length > MAX_MESSAGE_SIZE:
                    raise TapUnavailable(f"Oversized tap response: {length}")
                payload = self._recv_exact(sock, length) if length else b"{}"
            except OSError as e:
                raise TapUnavailable(str(e)) from e
            finally:
                try:
                    sock.close()
                except OSError:
                    pass

        response = json.loads(payload.decode("utf-8"))
        if response.get("status") != "success":
            raise TapUnavailable(response.get("error", "tap error"))
        return response.get("result", {})

    def _recv_exact(self, sock: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(min(8192, size - len(data)))
            if not chunk:
                raise OSError("Tap closed the connection")
            data.extend(chunk)
        return bytes(data)

    def check_version(self) -> dict[str, Any]:
        result = self.send("ping")
        version = result.get("tap_protocol_version")
        if version != EXPECTED_TAP_PROTOCOL and not self._warned_version:
            self._warned_version = True
            logger.warning(
                "Tap protocol v%s != expected v%s — rebuild the Tap device from "
                "the current m4l/tap.maxpat + tap_server.js",
                version,
                EXPECTED_TAP_PROTOCOL,
            )
        return result


AUDIO_LEVELS_TOOL = types.Tool(
    name="get_audio_levels",
    description=(
        "Loudness and tonal balance of the Master output, measured "
        "pre-master-fader by the optional 'AbletonMCP Tap' Max for Live "
        "device: stereo RMS/peak in dBFS (~300 ms power averaging, anti-phase "
        "safe), a clipping flag latched over a 5 s window (sample-peak), and "
        "10 resonant octave bands 31 Hz–16 kHz (a meter, not a spectrum "
        "analyzer). duration_seconds > 0 samples a window — launch clips or "
        "start the transport first, then sample, then stop. stale:true means "
        "the device stopped feeding (audio engine off). If the tap isn't "
        "installed this returns available:false with setup instructions — a "
        "Suite/M4L-only optional extra."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "duration_seconds": {
                "type": "number",
                "minimum": 0,
                "maximum": 10,
                "default": 0,
                "description": "0 = instant snapshot; >0 = sample for this long and aggregate",
            }
        },
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    outputSchema={
        "type": "object",
        "properties": {
            "available": {"type": "boolean"},
            "hint": {"type": "string"},
            "tap_protocol": {"type": "object"},
            "receiving_audio": {"type": "boolean"},
            "stale": {"type": "boolean"},
            "data_age_ms": {"type": ["number", "null"]},
            "rms_db": {"type": "number"},
            "peak_db": {"type": "array"},
            "clipping": {"type": "boolean"},
            "bands": {"type": "array"},
            "sampled_seconds": {"type": "number"},
            "samples": {"type": "integer"},
            "rms_db_max": {"type": "number"},
            "peak_db_max": {"type": "number"},
            "bands_max": {"type": "array"},
            "window_seconds": {"type": "number"},
            "window": {"type": "object"},
            "note": {"type": "string"},
        },
    },
)


def get_audio_levels(tap: TapClient, duration_seconds: float = 0) -> dict[str, Any]:
    """Tool handler body: instant snapshot, or sampled aggregate over a window."""
    try:
        info = tap.check_version()
        version = info.get("tap_protocol_version")
        if version != EXPECTED_TAP_PROTOCOL or info.get("legacy_msgs", 0) > 0:
            # A v1 device feeding a v2 server (or any stale patch/js combo)
            # would produce numbers that LOOK plausible and are wrong —
            # withhold them and say why. legacy_msgs counts old-protocol Max
            # messages the js refused to interpret.
            return {
                "available": False,
                "hint": REBUILD_HINT,
                "tap_protocol": {"device": version, "expected": EXPECTED_TAP_PROTOCOL},
            }

        if not duration_seconds:
            snapshot = tap.send("get_levels")
            snapshot["available"] = True
            return snapshot

        def _db(value: Any) -> float:
            # Defensive: a defective device build can emit null levels.
            return value if isinstance(value, (int, float)) else -70.0

        total = min(float(duration_seconds), 10.0)
        start = time.monotonic()
        # window_ms tracks elapsed sampling time, so the tap's gap-free 10 Hz
        # history covers exactly our window: transients between 250 ms polls
        # can't be missed, and a loud clip stopped just before sampling can't
        # pollute the stats (the old code both missed peaks and dropped the
        # window aggregate entirely).
        samples = [tap.send("get_levels", window_ms=100)]
        while time.monotonic() - start < total:
            time.sleep(0.25)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            samples.append(tap.send("get_levels", window_ms=max(100, min(elapsed_ms, 5000))))

        last = samples[-1]
        windows = [s.get("window") or {} for s in samples]
        n_bands = len(last.get("bands", []))
        band_max = [
            max(_db(s["bands"][i]["level_db"]) for s in samples if len(s.get("bands", [])) > i)
            for i in range(n_bands)
        ]
        return {
            "available": True,
            "sampled_seconds": round(total, 2),
            "samples": len(samples),
            "receiving_audio": any(s.get("receiving_audio") for s in samples),
            "stale": bool(last.get("stale", False)),
            "data_age_ms": last.get("data_age_ms"),
            "rms_db_max": max(_db(w.get("rms_max_db")) for w in windows),
            "peak_db_max": max(_db(w.get("peak_max_db")) for w in windows),
            "clipping": any(s.get("clipping") for s in samples),
            "bands_max": [
                {"hz": last["bands"][i]["hz"], "level_db": band_max[i]} for i in range(n_bands)
            ],
            "window_seconds": last.get("window_seconds"),
            "window": last.get("window"),
            "note": last.get("note", ""),
        }
    except TapUnavailable as e:
        logger.info(f"Tap unavailable: {e}")
        return {"available": False, "hint": UNAVAILABLE_HINT}
