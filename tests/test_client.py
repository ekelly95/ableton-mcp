"""AbletonClient: framing, reconnect-once, and the no-resend-on-timeout rule.

These tests exercise the TCP transport and say so (use_tcp=True): the
platform default is AF_UNIX off Windows, which would ignore the port and dial
/tmp/ableton_mcp.sock (the first macOS CI run failed exactly there). The
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
        assert client.send("get_tracks") == {"echo": "get_tracks"}
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
