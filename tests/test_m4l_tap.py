"""TapClient, the get_audio_levels tool, and Node framing conformance."""

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from mcp_server.m4l import TapClient, TapUnavailable, get_audio_levels
from tests.helpers import read_frame, write_frame

REPO_ROOT = Path(__file__).resolve().parent.parent

V2_BAND_LABELS = ["31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]


class FakeTapServer:
    """Python double of the Node tap: same framing, canned v2 levels."""

    def __init__(
        self,
        protocol_version: int = 2,
        receiving_audio: bool = True,
        legacy_msgs: int = 0,
    ):
        self.protocol_version = protocol_version
        self.receiving_audio = receiving_audio
        self.legacy_msgs = legacy_msgs
        self.requests = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _levels(self, window_ms: float = 5000):
        return {
            "receiving_audio": self.receiving_audio,
            "stale": False,
            "data_age_ms": 40,
            "rms_db": -18.5,
            "peak_db": [-6.0, -6.2],
            "clipping": False,
            "bands": [
                {"hz": label, "level_db": -30.0 + i} for i, label in enumerate(V2_BAND_LABELS)
            ],
            "window_seconds": 5,
            "window": {
                "rms_mean_db": -20.0,
                "rms_max_db": -17.0,
                "peak_max_db": -5.5,
                "clipping": False,
                "window_ms": window_ms,
            },
            "note": "test double",
        }

    def _run(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                while True:
                    request = read_frame(conn)
                    if request is None:
                        break
                    self.requests.append(request)
                    if request["type"] == "ping":
                        result = {
                            "pong": True,
                            "name": "AbletonMCP Tap",
                            "tap_protocol_version": self.protocol_version,
                            "bands": len(V2_BAND_LABELS),
                            "uptime_seconds": 1,
                            "legacy_msgs": self.legacy_msgs,
                        }
                    else:
                        window_ms = (request.get("params") or {}).get("window_ms", 5000)
                        result = self._levels(window_ms)
                    write_frame(conn, {"status": "success", "id": request["id"], "result": result})
            except OSError:
                pass
            finally:
                conn.close()

    def close(self):
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass


class TestTapClient:
    def test_ping_and_levels(self):
        server = FakeTapServer()
        client = TapClient(port=server.port)
        try:
            info = client.check_version()
            assert info["tap_protocol_version"] == 2
            levels = client.send("get_levels")
            assert levels["rms_db"] == -18.5
            assert len(levels["bands"]) == 10
        finally:
            server.close()

    def test_unreachable(self):
        client = TapClient(port=1)
        with pytest.raises(TapUnavailable):
            client.send("ping")


class TestGetAudioLevelsHandler:
    def test_instant_snapshot(self):
        server = FakeTapServer()
        try:
            result = get_audio_levels(TapClient(port=server.port))
            assert result["available"] is True
            assert result["receiving_audio"] is True
            assert result["stale"] is False
            assert result["bands"][0]["hz"] == "31"
        finally:
            server.close()

    def test_sampled_window(self):
        server = FakeTapServer()
        try:
            start = time.time()
            result = get_audio_levels(TapClient(port=server.port), duration_seconds=0.6)
            assert result["available"] is True
            assert result["samples"] >= 2
            # Aggregates come from the tap's gap-free window stats, not from
            # the polled instantaneous values.
            assert result["rms_db_max"] == -17.0
            assert result["peak_db_max"] == -5.5
            assert len(result["bands_max"]) == 10
            assert time.time() - start >= 0.5
        finally:
            server.close()

    def test_sampled_window_keeps_window_stats(self):
        # Regression: the duration path used to DROP the `window` aggregate
        # while the outputSchema still advertised it.
        server = FakeTapServer()
        try:
            result = get_audio_levels(TapClient(port=server.port), duration_seconds=0.3)
            assert result["window"]["rms_max_db"] == -17.0
            assert result["stale"] is False
            assert result["data_age_ms"] == 40
        finally:
            server.close()

    def test_sampled_window_sends_clamped_window_ms(self):
        server = FakeTapServer()
        try:
            get_audio_levels(TapClient(port=server.port), duration_seconds=0.6)
            gets = [r for r in server.requests if r["type"] == "get_levels"]
            assert len(gets) >= 2
            for r in gets:
                assert 100 <= r["params"]["window_ms"] <= 5000
            # Later polls widen the window to cover the elapsed sampling time.
            assert gets[-1]["params"]["window_ms"] >= 500
        finally:
            server.close()

    def test_v1_device_withholds_levels(self):
        # A v1 device feeding a v2 server would serve numbers that look
        # plausible and are wrong — the handler must refuse with the rebuild
        # hint instead.
        server = FakeTapServer(protocol_version=1)
        try:
            result = get_audio_levels(TapClient(port=server.port))
            assert result["available"] is False
            assert "Rebuild" in result["hint"]
            assert result["tap_protocol"] == {"device": 1, "expected": 2}
        finally:
            server.close()

    def test_legacy_patch_messages_withhold_levels(self):
        # v2 js + v1 patch: the js counts old rms/band messages instead of
        # interpreting them; any count means the DEVICE needs a rebuild.
        server = FakeTapServer(legacy_msgs=5)
        try:
            result = get_audio_levels(TapClient(port=server.port))
            assert result["available"] is False
            assert "Rebuild" in result["hint"]
        finally:
            server.close()

    def test_unavailable_is_graceful(self):
        result = get_audio_levels(TapClient(port=1))
        assert result["available"] is False
        assert "AbletonMCP Tap" in result["hint"]


@pytest.mark.anyio
class TestOverProtocol:
    async def test_tool_listed_and_graceful_absence(self):
        from mcp.shared.memory import create_connected_server_and_client_session

        from mcp_server.server import build_server
        from tests.helpers import FakeAbletonClient

        server = build_server(FakeAbletonClient(), tap=TapClient(port=1))
        async with create_connected_server_and_client_session(server) as session:
            tools = {t.name for t in (await session.list_tools()).tools}
            assert "get_audio_levels" in tools
            result = await session.call_tool("get_audio_levels", {})
            assert result.isError is False
            assert result.structuredContent["available"] is False

    async def test_tool_with_fake_tap(self):
        from mcp.shared.memory import create_connected_server_and_client_session

        from mcp_server.server import build_server
        from tests.helpers import FakeAbletonClient

        fake_tap = FakeTapServer()
        try:
            server = build_server(FakeAbletonClient(), tap=TapClient(port=fake_tap.port))
            async with create_connected_server_and_client_session(server) as session:
                result = await session.call_tool("get_audio_levels", {})
                assert result.structuredContent["available"] is True
                assert result.structuredContent["rms_db"] == -18.5
        finally:
            fake_tap.close()


# --- real-Node tests ---------------------------------------------------------

node_missing = shutil.which("node") is None


def _start_node_tap(port: int, extra_env: dict | None = None):
    """Start the REAL tap_server.js on a test port; returns (proc, client, ping).

    Ports deliberately != 9878: a real tap inside a running Live would collide
    (and answer!).
    """
    env = dict(os.environ, ABLETON_TAP_PORT=str(port))
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        ["node", str(REPO_ROOT / "m4l" / "tap_server.js")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
        env=env,
    )
    client = TapClient(port=port)
    for _ in range(20):  # allow bind retries/startup
        time.sleep(0.25)
        try:
            return proc, client, client.send("ping")
        except TapUnavailable:
            continue
    proc.terminate()
    proc.wait(timeout=10)
    raise AssertionError("node tap never answered")


@pytest.mark.skipif(node_missing, reason="node not installed")
def test_node_tap_server_framing_conformance():
    """Drive the REAL Node script with TapClient — cross-language framing."""
    proc, client, ping = _start_node_tap(19878)
    try:
        assert ping["pong"] is True
        assert ping["tap_protocol_version"] == 2
        assert ping["legacy_msgs"] == 0

        levels = client.send("get_levels")
        assert levels["receiving_audio"] is False  # no Max feeding it
        assert levels["stale"] is True
        assert levels["data_age_ms"] is None
        assert len(levels["bands"]) == 10
        assert levels["rms_db"] == -70.0  # clamp floor with no signal
        assert levels["clipping"] is False

        # Two concurrent clients must both be served (multi-client by design).
        second = TapClient(port=19878)
        assert second.send("ping")["pong"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(node_missing, reason="node not installed")
def test_node_tap_fed_levels_pin_v2_conventions():
    """The env-gated test feed pins the measurement conventions end to end:
    sqrt(power/2), band index mapping, clip latching, window_ms."""
    proc, client, _ping = _start_node_tap(19879, {"ABLETON_TAP_TEST_FEED": "1"})
    try:
        time.sleep(1.2)  # let the 100ms feed accumulate history
        levels = client.send("get_levels")
        assert levels["stale"] is False
        assert levels["data_age_ms"] is not None and levels["data_age_ms"] < 1000
        assert levels["receiving_audio"] is True
        # pow 0.02 => rms sqrt(0.02/2) = 0.1 => exactly -20.0 dB
        assert abs(levels["rms_db"] - (-20.0)) < 0.11
        by_hz = {b["hz"]: b["level_db"] for b in levels["bands"]}
        assert abs(by_hz["1k"] - (-20.0)) < 0.11
        assert abs(by_hz["63"] - (-60.0)) < 0.11
        # Current peaks read -6 dB, but the first ~300ms of feed was clipped:
        # the flag must LATCH from history, not track the last frame.
        assert abs(levels["peak_db"][0] - (-6.0)) < 0.11
        assert levels["clipping"] is True

        narrow = client.send("get_levels", window_ms=200)
        assert narrow["window"]["window_ms"] == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(node_missing, reason="node not installed")
def test_node_tap_goes_stale_when_feed_stops():
    """DSP stopping must flip stale, floor the values, and clear
    receiving_audio — v1 served frozen numbers forever."""
    proc, client, _ping = _start_node_tap(
        19880, {"ABLETON_TAP_TEST_FEED": "1", "ABLETON_TAP_TEST_FEED_STOP_MS": "600"}
    )
    try:
        deadline = time.time() + 5.0
        levels = None
        while time.time() < deadline:
            levels = client.send("get_levels")
            if levels["stale"]:
                break
            time.sleep(0.3)
        assert levels is not None and levels["stale"] is True
        assert levels["receiving_audio"] is False
        assert levels["rms_db"] == -70.0
        assert levels["peak_db"] == [-70.0, -70.0]
        assert all(b["level_db"] == -70.0 for b in levels["bands"])
    finally:
        proc.terminate()
        proc.wait(timeout=10)
