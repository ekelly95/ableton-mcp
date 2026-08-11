"""LAB (m4l-lab branch): Max for Live tap client + audio-levels tool.

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
EXPECTED_TAP_PROTOCOL = 1

logger = logging.getLogger("ableton-mcp.m4l")

UNAVAILABLE_HINT = (
    "Audio tap not detected. Drop the 'AbletonMCP Tap' Max Audio Effect on the "
    "Master track (requires Live Suite or Max for Live) — build steps in "
    "m4l/README-lab.md. Then ensure the device is powered on and Live's audio "
    "engine is running."
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

    def check_version(self) -> None:
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


AUDIO_LEVELS_TOOL = types.Tool(
    name="get_audio_levels",
    description=(
        "What the set SOUNDS like right now: loudness (RMS/peak, dBFS), "
        "clipping flag, and an 8-band frequency picture (60 Hz – 7.7 kHz), "
        "measured by the optional 'AbletonMCP Tap' Max for Live device on the "
        "Master track. duration_seconds > 0 samples a window (use while "
        "something plays: launch clips or start the transport first, then "
        "sample, then stop). If the tap device isn't installed this returns "
        "available:false with setup instructions — it is a Suite/M4L-only "
        "optional extra. Readings are pre-master-fader."
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
            "receiving_audio": {"type": "boolean"},
            "rms_db": {"type": "number"},
            "peak_db": {"type": "array"},
            "clipping": {"type": "boolean"},
            "bands": {"type": "array"},
            "window": {"type": "object"},
        },
    },
)


def get_audio_levels(tap: TapClient, duration_seconds: float = 0) -> dict[str, Any]:
    """Tool handler body: instant snapshot, or sampled aggregate over a window."""
    try:
        tap.check_version()
        snapshot = tap.send("get_levels")
        if not duration_seconds:
            snapshot["available"] = True
            return snapshot

        samples = [snapshot]
        deadline = time.time() + min(float(duration_seconds), 10.0)
        while time.time() < deadline:
            time.sleep(0.25)
            samples.append(tap.send("get_levels"))

        def _db(value: Any) -> float:
            # Defensive: a defective device build can emit null levels.
            return value if isinstance(value, (int, float)) else -70.0

        last = samples[-1]
        n_bands = len(last.get("bands", []))
        band_max = [
            max(_db(s["bands"][i]["level_db"]) for s in samples if len(s.get("bands", [])) > i)
            for i in range(n_bands)
        ]
        return {
            "available": True,
            "sampled_seconds": round(min(float(duration_seconds), 10.0), 2),
            "samples": len(samples),
            "receiving_audio": any(s.get("receiving_audio") for s in samples),
            "rms_db_max": max(_db(s.get("rms_db")) for s in samples),
            "peak_db_max": max(max(_db(p) for p in (s.get("peak_db") or [-70.0])) for s in samples),
            "clipping": any(s.get("clipping") for s in samples),
            "bands_max": [
                {"hz": last["bands"][i]["hz"], "level_db": band_max[i]} for i in range(n_bands)
            ],
            "note": last.get("note", ""),
        }
    except TapUnavailable as e:
        logger.info(f"Tap unavailable: {e}")
        return {"available": False, "hint": UNAVAILABLE_HINT}
