"""AbletonClient: framing, reconnect-once, and the no-resend-on-timeout rule.

These tests exercise the TCP transport and say so (use_tcp=True): the
platform default is AF_UNIX off Windows, which would ignore the port and dial
the default socket path (the first macOS CI run failed exactly there). The
AF_UNIX transport has its own suite in test_unix_socket.py."""

import pytest

from mcp_server.client import AbletonClient, AbletonConnectionError, CommandError
from tests.helpers import ScriptedServer


def test_send_round_trip():
    server = ScriptedServer.tcp(["serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        assert client.send("hello") == {"echo": "hello"}
        assert client.send("again") == {"echo": "again"}
        assert len(server.requests_received) == 2
    finally:
        client.close()
        server.close()


def test_command_error_taxonomy():
    server = ScriptedServer.tcp(["serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        with pytest.raises(CommandError) as exc:
            client.send("explode")
        assert exc.value.error_type == "LiveAPIError"
    finally:
        client.close()
        server.close()


def test_partial_apply_error_reaches_caller_with_applied():
    server = ScriptedServer.tcp(["serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        with pytest.raises(CommandError) as exc:
            client.send("partial")
        assert exc.value.error_type == "PartialApplyError"
        assert exc.value.applied == ["name", "volume"]
    finally:
        client.close()
        server.close()


def test_reconnects_once_after_dead_socket():
    # First connection dies immediately (Live restarted); second serves.
    # Uses a read-only command: only those are eligible for auto-resend.
    server = ScriptedServer.tcp(["drop", "serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        assert client.send("get_transport_state") == {"echo": "get_transport_state"}
    finally:
        client.close()
        server.close()


def test_cannot_connect_is_helpful():
    client = AbletonClient(use_tcp=True, port=1)  # nothing listens there
    with pytest.raises(AbletonConnectionError, match="Ableton Live running"):
        client.send("anything")


def test_timeout_does_not_resend():
    server = ScriptedServer.tcp(["stall", "serve"])
    client = AbletonClient(use_tcp=True, port=server.port, timeout=0.3)
    try:
        with pytest.raises(AbletonConnectionError, match="No response within"):
            client.send("slow_thing")
        # The stalled server got the request exactly once — no dangerous resend.
        assert len(server.requests_received) == 1
    finally:
        client.close()
        server.close()


def test_read_only_command_resends_after_delivered_but_lost_response():
    # get_session_overview is read_only in the registry → safe to resend.
    server = ScriptedServer.tcp(["read_then_die", "serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        result = client.send("get_session_overview")
        assert result == {"echo": "get_session_overview"}
        assert len(server.requests_received) == 2  # original + safe resend
    finally:
        client.close()
        server.close()


def test_write_command_never_resends_after_delivered_but_lost_response():
    # delete_track is destructive → the client must refuse to auto-resend.
    server = ScriptedServer.tcp(["read_then_die", "serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        with pytest.raises(AbletonConnectionError, match="may or may not have executed"):
            client.send("delete_track", track_index=0)
        assert len(server.requests_received) == 1  # delivered exactly once
    finally:
        client.close()
        server.close()


def test_wire_specials_resend():
    server = ScriptedServer.tcp(["read_then_die", "serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        assert client.send("ping") == {"echo": "ping"}
        assert len(server.requests_received) == 2
    finally:
        client.close()
        server.close()


def test_second_connection_loss_is_translated():
    # The connection dies on the first attempt AND on the reconnect retry:
    # the caller must still get the helpful hint, not a raw OSError.
    server = ScriptedServer.tcp(["read_then_die", "drop"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        with pytest.raises(AbletonConnectionError, match="Ableton Live running"):
            client.send("get_transport_state")
    finally:
        client.close()
        server.close()


def test_malformed_response_is_translated_and_never_resent():
    # A framed non-JSON reply must surface as AbletonConnectionError — and
    # must not trigger the read-only resend path (garbage isn't fixed by
    # asking again). A second connection is scripted on purpose: with only one,
    # the server is closed by the time a resend could happen, so the
    # "delivered exactly once" assertion would hold no matter what the client did.
    server = ScriptedServer.tcp(["garbage", "serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        with pytest.raises(AbletonConnectionError, match="malformed response"):
            client.send("get_transport_state")
        assert len(server.requests_received) == 1
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("behaviour", ["empty_frame", "wrong_shape"])
def test_reply_that_is_not_the_protocol_is_never_reported_as_a_refusal(behaviour):
    """A well-framed reply that isn't ours must not become CommandError.

    CommandError means "the control surface executed this and reported a
    failure". Raising it for an empty or foreign reply tells the model a
    destructive write reached Live and was refused — so it treats the session
    as untouched — when nothing ever arrived. That is the one direction this
    must never fail in.
    """
    server = ScriptedServer.tcp([behaviour, "serve"])
    client = AbletonClient(use_tcp=True, port=server.port)
    try:
        with pytest.raises(AbletonConnectionError, match="not the bridge protocol"):
            client.send("delete_track", track_index=0)
        assert len(server.requests_received) == 1  # not resent
    finally:
        client.close()
        server.close()


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("ABLETON_MCP_HOST", raising=False)
    monkeypatch.delenv("ABLETON_MCP_PORT", raising=False)
    client = AbletonClient.from_env()
    default = AbletonClient()
    assert (client.host, client.port, client.use_tcp) == (
        default.host,
        default.port,
        default.use_tcp,
    )


def test_from_env_host_forces_tcp(monkeypatch):
    # A remote Live (e.g. a Windows VM) is only reachable over TCP, even from
    # a platform whose default transport is the Unix socket.
    monkeypatch.setenv("ABLETON_MCP_HOST", "192.0.2.10")
    monkeypatch.setenv("ABLETON_MCP_PORT", "19877")
    client = AbletonClient.from_env()
    assert (client.host, client.port, client.use_tcp) == ("192.0.2.10", 19877, True)


def test_from_env_round_trip_over_tcp(monkeypatch):
    server = ScriptedServer.tcp(["serve"])
    monkeypatch.setenv("ABLETON_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("ABLETON_MCP_PORT", str(server.port))
    client = AbletonClient.from_env()
    try:
        assert client.send("hello") == {"echo": "hello"}
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("raw", ["abc", "0", "70000"])
def test_from_env_rejects_bad_port(monkeypatch, raw):
    monkeypatch.setenv("ABLETON_MCP_PORT", raw)
    with pytest.raises(ValueError, match="ABLETON_MCP_PORT"):
        AbletonClient.from_env()
