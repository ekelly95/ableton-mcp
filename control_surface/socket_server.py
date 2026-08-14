"""Socket server: accepts length-prefixed JSON commands and executes them.

Serves ONE client at a time, serially — a deliberate simplification. The MCP
server is the only intended client and serializes its own requests; a second
concurrent client would indicate a misconfiguration (two MCP servers), which
surfaces naturally as the second connection queueing behind the first.

Wire protocol (unchanged from 1.0):
    frame    = 4-byte big-endian length + UTF-8 JSON
    request  = {"type": <command>, "params": {...}, "id": <uuid>}
    response = {"status": "success", "result": <any>, "id": <uuid>}
             | {"status": "error", "error": <str>, "error_type": <name>,
                "id": <uuid>} plus, when applicable: "param" (ValidationError),
                "applied" (PartialApplyError), "timeout" (marshal timeouts)
"""

import json
import os
import socket
import struct
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from typing import Any

from .config import (
    HEADER_SIZE,
    MAX_MESSAGE_SIZE,
    SOCKET_ACCEPT_TIMEOUT,
    SOCKET_BACKLOG,
    SOCKET_BUFFER_SIZE,
    SOCKET_PATH,
    TCP_HOST,
    TCP_PORT,
    USE_TCP,
    VERSION,
)
from .log import get_logger, get_operation_logger
from .registry import REGISTRY, LiveAPIError, ValidationError
from .thread_marshal import MainThreadExecutionError, ThreadMarshaler

logger = get_logger("socket_server")

# Ring size for request-id deduplication (idempotent replay protection).
RECENT_RESPONSES_MAX = 64


class CommandContext:
    """Access to Live for command handlers.

    song/app are properties, not snapshots: handlers run on Live's main thread
    (marshaled), so resolving the handles there keeps every Live API touch on
    the main thread.
    """

    def __init__(self, control_surface: Any):
        self.control_surface = control_surface

    @property
    def song(self) -> Any:
        return self.control_surface.song()

    @property
    def app(self) -> Any:
        return self.control_surface.application()


class SocketServer:
    def __init__(
        self,
        control_surface: Any,
        host: str | None = None,
        port: int | None = None,
        socket_path: str | None = None,
        use_tcp: bool | None = None,
        registry=None,
    ):
        self._control_surface = control_surface
        self._operation_logger = get_operation_logger()
        # The marshaler journals expired/late task outcomes that the normal
        # request/response path can no longer report.
        self._marshaler = ThreadMarshaler(control_surface, operation_logger=self._operation_logger)
        self._registry = registry if registry is not None else REGISTRY

        self._host = host if host is not None else TCP_HOST
        self._port = port if port is not None else TCP_PORT
        self._socket_path = socket_path if socket_path is not None else SOCKET_PATH
        self._use_tcp = use_tcp if use_tcp is not None else USE_TCP

        self._socket: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        # Accepted client sockets, guarded by their OWN lock: stop() holds
        # self._lock while joining the server thread, so the server thread
        # must never need self._lock to make progress.
        self._clients_lock = threading.Lock()
        self._client_sockets: set[socket.socket] = set()
        # id -> response: a client that lost the response and resends the SAME
        # request must get the cached answer, not a second execution.
        self._recent_responses: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @property
    def bound_port(self) -> int | None:
        """Actual bound TCP port (differs from requested when binding port 0 in tests)."""
        if self._socket is not None and self._use_tcp:
            return self._socket.getsockname()[1]
        return None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                logger.warning("Server already running")
                return
            try:
                self._create_socket()
                self._running = True
                self._server_thread = threading.Thread(
                    target=self._run,
                    name="AbletonMCP-SocketServer",
                    daemon=True,
                )
                self._server_thread.start()
                addr = (
                    f"TCP {self._host}:{self.bound_port}"
                    if self._use_tcp
                    else f"Unix {self._socket_path}"
                )
                logger.info(f"Socket server started on {addr}")
            except Exception as e:
                logger.error(f"Failed to start socket server: {e}")
                self._cleanup_socket()
                raise

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._cleanup_socket()
            # Unblock any handler parked in a blocking recv: closing the
            # socket from this thread makes that recv raise OSError, which
            # _handle_client treats as clean shutdown once _running is False.
            # Without this, the server thread stayed stuck in recv for as long
            # as a client was connected ("did not stop cleanly" on every Live
            # script reload).
            with self._clients_lock:
                clients = list(self._client_sockets)
                self._client_sockets.clear()
            for client in clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    client.close()
                except OSError:
                    pass
            if self._server_thread and self._server_thread.is_alive():
                self._server_thread.join(timeout=5.0)
                if self._server_thread.is_alive():
                    logger.warning("Server thread did not stop cleanly")
            logger.info("Socket server stopped")

    def _create_socket(self) -> None:
        if self._use_tcp:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # SO_REUSEADDR means the OPPOSITE thing on Windows: it lets any
            # other process bind a port already in use and take over new
            # connections — measured on Windows 11, the second bind succeeds
            # and steals the port. TCP is the Windows-only transport here, so
            # the exclusive option is the correct one; do not "restore"
            # SO_REUSEADDR. Measured too: the exclusive option still rebinds
            # immediately after a full accept/teardown cycle, so the Live
            # script-reload path is unaffected. SO_REUSEADDR stays for the
            # non-Windows TCP path the tests exercise, where its POSIX meaning
            # (rebind over TIME_WAIT) is the harmless and useful one.
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            self._socket.setsockopt(
                socket.SOL_SOCKET, exclusive if exclusive else socket.SO_REUSEADDR, 1
            )
            self._socket.bind((self._host, self._port))
        else:
            if os.path.exists(self._socket_path):
                try:
                    os.unlink(self._socket_path)
                except OSError as e:
                    logger.warning(f"Could not remove existing socket: {e}")
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.bind(self._socket_path)
            # Owner only. 0o660 granted the whole group, and on macOS every
            # ordinary account is in 'staff' — that handed every local user a
            # connection to Live. Both halves run as the same user, so no
            # group access is needed.
            os.chmod(self._socket_path, 0o600)

        self._socket.listen(SOCKET_BACKLOG)
        self._socket.settimeout(SOCKET_ACCEPT_TIMEOUT)

    def _cleanup_socket(self) -> None:
        if self._socket:
            try:
                self._socket.close()
            except Exception as e:
                logger.error(f"Error closing socket: {e}")
            self._socket = None
        if not self._use_tcp and os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except OSError as e:
                logger.warning(f"Could not remove socket file: {e}")

    def _run(self) -> None:
        while self._running:
            try:
                try:
                    client_socket, _ = self._socket.accept()
                except TimeoutError:
                    continue
                except OSError as e:
                    if self._running:
                        logger.error(f"Accept error: {e}")
                    break

                with self._clients_lock:
                    if not self._running:
                        try:
                            client_socket.close()
                        except OSError:
                            pass
                        break
                    self._client_sockets.add(client_socket)

                try:
                    self._handle_client(client_socket)
                except Exception as e:
                    logger.error(f"Error handling client: {e}\n{traceback.format_exc()}")
                finally:
                    with self._clients_lock:
                        self._client_sockets.discard(client_socket)
                    try:
                        client_socket.close()
                    except Exception:
                        pass
            except Exception as e:
                if self._running:
                    logger.error(f"Server loop error: {e}\n{traceback.format_exc()}")

    def _handle_client(self, client_socket: socket.socket) -> None:
        client_socket.settimeout(None)
        while self._running:
            try:
                message = self._read_message(client_socket)
                if message is None:
                    break
                response = self._process_message(message)
                self._send_message(client_socket, response)
            except (ConnectionResetError, BrokenPipeError):
                logger.debug("Client disconnected")
                break
            except OSError as e:
                # On Windows, stop() closing this socket from another thread
                # surfaces here as OSError on the blocked recv — that is the
                # designed shutdown path, not an error.
                if not self._running:
                    logger.debug("Client socket closed during shutdown")
                else:
                    logger.error(f"Client socket error: {e}")
                break
            except Exception as e:
                logger.error(f"Error in client handler: {e}")
                try:
                    self._send_message(
                        client_socket,
                        {"status": "error", "error": str(e), "error_type": type(e).__name__},
                    )
                except Exception:
                    pass
                break

    def _read_message(self, sock: socket.socket) -> dict[str, Any] | None:
        header = self._recv_exact(sock, HEADER_SIZE)
        if header is None:
            return None
        msg_length = struct.unpack(">I", header)[0]
        if msg_length > MAX_MESSAGE_SIZE:
            raise ValueError(f"Message too large: {msg_length} > {MAX_MESSAGE_SIZE}")
        if msg_length == 0:
            return {}
        body = self._recv_exact(sock, msg_length)
        if body is None:
            return None
        try:
            message = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e
        if not isinstance(message, dict):
            # A scalar, array or `null` is well-formed JSON but not a request.
            # Without this, _process_message's message.get() raised a bare
            # AttributeError — and a literal `null` parsed to None, which
            # _handle_client reads as end-of-stream, so the peer was hung up on
            # with no answer at all. Same treatment as invalid JSON: a peer
            # framing this is not speaking the protocol.
            raise ValueError(f"Request must be a JSON object, got {type(message).__name__}")
        return message

    def _recv_exact(self, sock: socket.socket, size: int) -> bytes | None:
        data = bytearray()
        while len(data) < size:
            try:
                chunk = sock.recv(min(SOCKET_BUFFER_SIZE, size - len(data)))
            except TimeoutError:
                # Dead branch while client sockets have no timeout (the accept
                # handler calls settimeout(None)), but if one is ever
                # configured this must not busy-spin through shutdown.
                if not self._running:
                    return None
                continue
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _send_message(self, sock: socket.socket, message: dict[str, Any]) -> None:
        body = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
        if len(body) > MAX_MESSAGE_SIZE:
            raise ValueError(f"Response too large: {len(body)} > {MAX_MESSAGE_SIZE}")
        sock.sendall(struct.pack(">I", len(body)) + body)

    def _process_message(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            # No id, or an id we cannot use as a cache key: mint one for the
            # response and skip the duplicate cache — a generated key can never
            # match a retry, so caching under it would only evict real entries
            # from the bounded ring. The isinstance check is what keeps an
            # unhashable id (a list, say) from raising TypeError here, which
            # escaped into the connection-level handler and hung up on the
            # client instead of answering.
            return self._execute_message(message, str(uuid.uuid4()))

        cached = self._recent_responses.get(request_id)
        if cached is not None:
            logger.warning(f"Duplicate request id {request_id} — replaying cached response")
            return cached

        response = self._execute_message(message, request_id)

        # Timeout responses are NOT cached: the marshal's deadline refusal
        # guarantees a timed-out request never executed, so a client retrying
        # the SAME id deserves a fresh attempt, not a replay of the stale
        # refusal. (The shipped client mints a new uuid per send — this is
        # insurance for any client that retries on a stable id.)
        if not response.get("timeout"):
            self._recent_responses[request_id] = response
            while len(self._recent_responses) > RECENT_RESPONSES_MAX:
                self._recent_responses.popitem(last=False)
        return response

    def _execute_message(self, message: dict[str, Any], request_id: str) -> dict[str, Any]:
        start_time = time.time()
        command_type = message.get("type")
        params = message.get("params", {})
        response: dict[str, Any] = {"id": request_id}

        try:
            if command_type == "ping":
                response["status"] = "success"
                response["result"] = {
                    "pong": True,
                    "version": VERSION,
                    "schema_hash": self._registry.schema_hash(),
                    "command_count": len(self._registry),
                }
                return response

            if command_type == "list_commands":
                response["status"] = "success"
                response["result"] = {
                    "commands": self._registry.list_commands(),
                    "categories": {
                        cat: self._registry.list_by_category(cat)
                        for cat in self._registry.get_categories()
                    },
                }
                return response

            if command_type == "get_mcp_tools":
                response["status"] = "success"
                response["result"] = {
                    "tools": self._registry.generate_mcp_tools(),
                    "schema_hash": self._registry.schema_hash(),
                }
                return response

            schema = self._registry.get(command_type)
            if schema is None:
                raise ValidationError(f"Unknown command: {command_type}")

            validated_params = schema.validate_params(params)
            ctx = CommandContext(self._control_surface)

            # One marshal per request: the entire handler runs as a single
            # task on Live's main thread, reads included.
            result = self._marshaler.execute(
                schema.handler,
                ctx,
                timeout=schema.timeout,
                command=command_type,
                **validated_params,
            )

            duration_ms = (time.time() - start_time) * 1000
            response["status"] = "success"
            response["result"] = result
            self._operation_logger.log(command_type, params, result, duration_ms)

        except ValidationError as e:
            self._fail(response, e, "ValidationError", command_type, params, start_time)
            if e.param:
                response["param"] = e.param

        except LiveAPIError as e:
            # type name, not the literal "LiveAPIError": PartialApplyError is a
            # subclass and its identity plus `applied` list must reach the wire
            # so a caller can tell which writes landed before the failure.
            self._fail(response, e, type(e).__name__, command_type, params, start_time)
            applied = getattr(e, "applied", None)
            if applied is not None:
                response["applied"] = applied

        except MainThreadExecutionError as e:
            self._fail(response, e, "MainThreadExecutionError", command_type, params, start_time)
            response["timeout"] = e.timeout

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Command error: {e}\n{tb}")
            self._fail(
                response, e, type(e).__name__, command_type, params, start_time, stack_trace=tb
            )

        return response

    def _fail(
        self,
        response: dict[str, Any],
        error: Exception,
        error_type: str,
        command_type: Any,
        params: dict[str, Any],
        start_time: float,
        stack_trace: str | None = None,
    ) -> None:
        """Fill in the error half of a response and journal it."""
        duration_ms = (time.time() - start_time) * 1000
        response["status"] = "error"
        response["error"] = str(error)
        response["error_type"] = error_type
        self._operation_logger.log_error(
            command_type or "unknown",
            params,
            str(error),
            error_type,
            duration_ms,
            stack_trace=stack_trace,
        )
