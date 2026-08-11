"""LAB: TapClient, the get_audio_levels tool, and Node framing conformance."""

import json
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from mcp_server.m4l import TapClient, TapUnavailable, get_audio_levels

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeTapServer:
    """Python double of the Node tap: same framing, canned levels."""

    def __init__(self, protocol_version: int = 1, receiving_audio: bool = True):
        self.protocol_version = protocol_version
        self.receiving_audio = receiving_audio
        self.requests = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _levels(self):
        return {
            "receiving_audio": self.receiving_audio,
            "rms_db": -18.5,
            "peak_db": [-6.0, -6.2],
            "clipping": False,
            "bands": [
                {"hz": label, "level_db": -30.0 + i}
                for i, label in enumerate(["60", "120", "240", "480", "960", "1.9k", "3.8k", "7.7k"])
            ],
            "window_seconds": 5,
            "window": {"rms_mean_db": -20.0, "rms_max_db": -17.0, "peak_max_db": -5.5},
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
                    header = b""
                    while len(header) < 4:
                        chunk = conn.recv(4 - len(header))
                        if not chunk:
                            raise OSError("closed")
                        header += chunk
                    length = struct.unpack(">I", header)[0]
                    payload = b""
                    while len(payload) < length:
                        chunk = conn.recv(length - len(payload))
                        if not chunk:
                            raise OSError("closed")
                        payload += chunk
                    request = json.loads(payload.decode())
                    self.requests.append(request)
                    if request["type"] == "ping":
                        result = {
                            "pong": True,
                            "name": "AbletonMCP Tap",
                            "tap_protocol_version": self.protocol_version,
                            "uptime_seconds": 1,
                        }
                    else:
                        result = self._levels()
                    response = {"status": "success", "id": request["id"], "result": result}
                    body = json.dumps(response).encode()
                    conn.sendall(struct.pack(">I", len(body)) + body)
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
            client.check_version()
            levels = client.send("get_levels")
            assert levels["rms_db"] == -18.5
            assert len(levels["bands"]) == 8
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
            assert result["bands"][0]["hz"] == "60"
        finally:
            server.close()

    def test_sampled_window(self):
        server = FakeTapServer()
        try:
            start = time.time()
            result = get_audio_levels(TapClient(port=server.port), duration_seconds=0.6)
            assert result["available"] is True
            assert result["samples"] >= 2
            assert result["rms_db_max"] == -18.5
            assert len(result["bands_max"]) == 8
            assert time.time() - start >= 0.5
        finally:
            server.close()

    def test_unavailable_is_graceful(self):
        result = get_audio_levels(TapClient(port=1))
        assert result["available"] is False
        assert "AbletonMCP Tap" in result["hint"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
class TestOverProtocol:
    async def test_tool_listed_and_graceful_absence(self):
        from mcp.shared.memory import create_connected_server_and_client_session

        from mcp_server.server import build_server
        from tests.test_server_tools import FakeAbletonClient

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
        from tests.test_server_tools import FakeAbletonClient

        fake_tap = FakeTapServer()
        try:
            server = build_server(FakeAbletonClient(), tap=TapClient(port=fake_tap.port))
            async with create_connected_server_and_client_session(server) as session:
                result = await session.call_tool("get_audio_levels", {})
                assert result.structuredContent["available"] is True
                assert result.structuredContent["rms_db"] == -18.5
        finally:
            fake_tap.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_tap_server_framing_conformance():
    """Drive the REAL Node script with TapClient — cross-language framing."""
    proc = subprocess.Popen(
        ["node", str(REPO_ROOT / "m4l" / "tap_server.js")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    try:
        client = TapClient()  # real port 9878
        result = None
        for _ in range(20):  # allow bind retries/startup
            time.sleep(0.25)
            try:
                result = client.send("ping")
                break
            except TapUnavailable:
                continue
        assert result is not None, f"node tap never answered: {proc.stderr.peek()[:200] if proc.stderr else ''}"
        assert result["pong"] is True
        assert result["tap_protocol_version"] == 1

        levels = client.send("get_levels")
        assert levels["receiving_audio"] is False  # no Max feeding it
        assert len(levels["bands"]) == 8
        assert levels["rms_db"] == -70.0  # clamp floor with no signal

        # Two concurrent clients must both be served (multi-client by design).
        second = TapClient()
        assert second.send("ping")["pong"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=10)
