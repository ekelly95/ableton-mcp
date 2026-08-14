"""Socket client for the control surface running inside Live.

Persistent connection with lazy connect and one reconnect-and-resend attempt
(covers Live being restarted between tool calls). A lock serializes sends:
the control surface serves one client serially, so concurrent MCP tool calls
must queue here rather than interleave frames.
"""

import json
import logging
import socket
import struct
import threading
import uuid
from typing import Any

from control_surface.commands import REGISTRY
from control_surface.config import (
    COMMAND_TIMEOUTS,
    HEADER_SIZE,
    MAX_MESSAGE_SIZE,
    SOCKET_PATH,
    TCP_HOST,
    TCP_PORT,
    USE_TCP,
)

# Answered by the socket server without touching Live state.
WIRE_SPECIALS = {"ping", "list_commands", "get_mcp_tools"}


def _safe_to_resend(command: str) -> bool:
    """Only read-only commands may be resent automatically: a connection that
    dies while we wait for the response means the request WAS delivered and may
    have executed — resending a write could run it twice."""
    if command in WIRE_SPECIALS:
        return True
    schema = REGISTRY.get(command)
    return schema is not None and schema.read_only


DEFAULT_TIMEOUT = 45.0
# Client-side grace on top of the control surface's own per-command timeout,
# so Live's timeout error (the informative one) wins the race when both fire.
TIMEOUT_GRACE = 15.0
CONNECT_TIMEOUT = 5.0
# Two-phase seeks resolve on the next ~100ms tick; more retries than this
# means the playhead is not settling and the caller should see the raw phase.
SEEK_RESEND_LIMIT = 4

logger = logging.getLogger("ableton-mcp.client")

NOT_RUNNING_HINT = (
    "Is Ableton Live running with the AbletonMCP control surface enabled? "
    "(Preferences > Link, Tempo & MIDI > Control Surface > AbletonMCP)"
)


class AbletonConnectionError(Exception):
    """Live is unreachable (not running, script disabled, or port blocked)."""


class CommandError(Exception):
    """The control surface executed the request and reported a failure."""

    def __init__(
        self,
        message: str,
        error_type: str = "unknown",
        param: str | None = None,
        applied: list | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.param = param
        # For PartialApplyError responses: which batch fields landed before the
        # failure. The message already narrates it; this is the structured copy.
        self.applied = applied

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_type != "unknown":
            parts.append(f"[{self.error_type}]")
        if self.param:
            parts.append(f"(param: {self.param})")
        return " ".join(parts)


class AbletonClient:
    def __init__(
        self,
        host: str = TCP_HOST,
        port: int = TCP_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        socket_path: str = SOCKET_PATH,
        use_tcp: bool = USE_TCP,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket_path = socket_path
        self.use_tcp = use_tcp
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    # -- connection management -------------------------------------------------

    def _endpoint(self) -> str:
        return f"{self.host}:{self.port}" if self.use_tcp else self.socket_path

    def _connect(self) -> None:
        # Mirrors the control surface's _create_socket branch: TCP on Windows,
        # Unix socket elsewhere (config.USE_TCP decides; both are overridable
        # so tests can exercise either transport on any platform).
        try:
            if self.use_tcp:
                sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
            else:
                if not hasattr(socket, "AF_UNIX"):
                    raise AbletonConnectionError(
                        "Unix sockets are unavailable on this platform; use use_tcp=True"
                    )
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    sock.settimeout(CONNECT_TIMEOUT)
                    sock.connect(self.socket_path)
                except OSError:
                    sock.close()
                    raise
        except OSError as e:
            raise AbletonConnectionError(
                f"Cannot connect to {self._endpoint()}. {NOT_RUNNING_HINT} ({e})"
            ) from e
        sock.settimeout(self.timeout)
        self._socket = sock
        logger.info(f"Connected to control surface at {self._endpoint()}")

    def _disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def close(self) -> None:
        with self._lock:
            self._disconnect()

    # -- request/response ------------------------------------------------------

    def send(self, command: str, **params: Any) -> Any:
        """Send a command and return its result. Thread-safe; serialized."""
        request = {"type": command, "params": params, "id": str(uuid.uuid4())}
        # Heavy commands (load_item etc.) get their declared budget + grace;
        # everything else uses the flat default.
        per_command = COMMAND_TIMEOUTS.get(command)
        effective_timeout = (
            max(self.timeout, per_command + TIMEOUT_GRACE) if per_command else self.timeout
        )
        with self._lock:
            if self._socket is None:
                self._connect()
            self._socket.settimeout(effective_timeout)
            try:
                response = self._round_trip(request)
            except OSError as e:
                self._disconnect()
                if not _safe_to_resend(command):
                    # The request may have been delivered and executed before
                    # the connection died; resending could run a write twice.
                    # (The control surface also dedupes by request id, but a
                    # Live restart clears that — so writes never auto-resend.)
                    raise AbletonConnectionError(
                        f"Connection lost after sending '{command}' — it may or may "
                        f"not have executed. Verify the current state with "
                        f"get_session_overview or the relevant get_* tool, then "
                        f"retry deliberately. ({e})"
                    ) from e
                logger.warning(f"Connection lost during '{command}', reconnecting once...")
                self._connect()
                try:
                    response = self._round_trip(request)
                except OSError as retry_error:
                    # One resend is policy; a second loss in a row means Live
                    # is not coming back right now — translate instead of
                    # leaking the raw socket error to the caller.
                    self._disconnect()
                    raise AbletonConnectionError(
                        f"Connection lost again while retrying '{command}'. "
                        f"{NOT_RUNNING_HINT} ({retry_error})"
                    ) from retry_error

        if response.get("status") == "success":
            return response.get("result")
        raise CommandError(
            message=response.get("error", "Unknown error"),
            error_type=response.get("error_type", "unknown"),
            param=response.get("param"),
            applied=response.get("applied"),
        )

    def send_resolving_seek(self, command: str, **params: Any) -> Any:
        """send(), repeated while the control surface answers {"phase": "seeking"}.

        Two-phase commands (create_locator, transport_control with a position)
        apply the playhead seek between scheduled tasks and ask to be called
        again; looping here lets callers see a single round trip.
        """
        result = self.send(command, **params)
        attempts = 0
        while (
            isinstance(result, dict)
            and result.get("phase") == "seeking"
            and attempts < SEEK_RESEND_LIMIT
        ):
            attempts += 1
            result = self.send(command, **params)
        return result

    def ping(self) -> dict[str, Any] | None:
        try:
            result = self.send("ping")
            return result if isinstance(result, dict) and result.get("pong") else None
        except (AbletonConnectionError, CommandError):
            return None

    def _round_trip(self, request: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_MESSAGE_SIZE:
            raise ValueError(f"Request too large: {len(body)} bytes")
        try:
            self._socket.sendall(struct.pack(">I", len(body)) + body)
            header = self._recv_exact(HEADER_SIZE)
            length = struct.unpack(">I", header)[0]
            if length > MAX_MESSAGE_SIZE:
                raise ValueError(f"Response too large: {length} bytes")
            payload = self._recv_exact(length) if length else b"{}"
        except TimeoutError as e:
            # Do NOT resend after a timeout: the command may still be running
            # inside Live, and a resend would queue it twice.
            waited = self._socket.gettimeout() if self._socket else None
            self._disconnect()
            raise AbletonConnectionError(
                f"No response within {waited}s — Live may be busy (modal dialog, "
                f"loading) or the command is very slow. Connection reset."
            ) from e
        try:
            response = json.loads(payload.decode("utf-8"))
        except ValueError as e:
            # Correctly framed but undecodable payload. Not raised as OSError
            # on purpose: resending won't fix a peer that speaks garbage, so
            # this must not enter send()'s reconnect-and-resend branch.
            self._disconnect()
            raise AbletonConnectionError(
                f"Received a malformed response — is something other than the "
                f"AbletonMCP control surface listening on this port? ({e})"
            ) from e
        # Decodable but not our protocol: an empty frame, a bare array, or any
        # object without our status field. This MUST NOT fall through to
        # send()'s error branch, which raises CommandError — the class that
        # means "the control surface executed this and reported a failure".
        # Telling the model a delete was refused, when nothing ever reached
        # Live, is wrong in the dangerous direction: it treats the session as
        # untouched. A wrong-shaped reply means a wrong peer, so it belongs
        # with the malformed case above.
        if not isinstance(response, dict) or response.get("status") not in ("success", "error"):
            self._disconnect()
            raise AbletonConnectionError(
                f"Received a reply that is not the bridge protocol — is something "
                f"other than the AbletonMCP control surface listening on this port? "
                f"({str(response)[:120]})"
            )
        return response

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self._socket.recv(min(8192, size - len(data)))
            if not chunk:
                raise OSError("Connection closed by control surface")
            data.extend(chunk)
        return bytes(data)
