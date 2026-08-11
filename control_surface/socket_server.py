"""Socket server: accepts length-prefixed JSON commands and executes them.

Serves ONE client at a time, serially — a deliberate simplification. The MCP
server is the only intended client and serializes its own requests; a second
concurrent client would indicate a misconfiguration (two MCP servers), which
surfaces naturally as the second connection queueing behind the first.

Wire protocol (unchanged from 1.0):
    frame    = 4-byte big-endian length + UTF-8 JSON
    request  = {"type": <command>, "params": {...}, "id": <uuid>}
    response = {"status": "success"|"error", "result"|... , "id": <uuid>}
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
from typing import Any, Dict, Optional

# Ring size for request-id deduplication (idempotent replay protection).
RECENT_RESPONSES_MAX = 64

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
        host: Optional[str] = None,
        port: Optional[int] = None,
        socket_path: Optional[str] = None,
        use_tcp: Optional[bool] = None,
        registry=None,
    ):
        self._control_surface = control_surface
        self._marshaler = ThreadMarshaler(control_surface)
        self._operation_logger = get_operation_logger()
        self._registry = registry if registry is not None else REGISTRY

        self._host = host if host is not None else TCP_HOST
        self._port = port if port is not None else TCP_PORT
        self._socket_path = socket_path if socket_path is not None else SOCKET_PATH
        self._use_tcp = use_tcp if use_tcp is not None else USE_TCP

        self._socket: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        # id -> response: a client that lost the response and resends the SAME
        # request must get the cached answer, not a second execution.
        self._recent_responses: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    @property
    def bound_port(self) -> Optional[int]:
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
            if self._server_thread and self._server_thread.is_alive():
                self._server_thread.join(timeout=5.0)
                if self._server_thread.is_alive():
                    logger.warning("Server thread did not stop cleanly")
            logger.info("Socket server stopped")

    def _create_socket(self) -> None:
        if self._use_tcp:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind((self._host, self._port))
        else:
            if os.path.exists(self._socket_path):
                try:
                    os.unlink(self._socket_path)
                except OSError as e:
                    logger.warning(f"Could not remove existing socket: {e}")
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.bind(self._socket_path)
            os.chmod(self._socket_path, 0o660)

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
                except socket.timeout:
                    continue
                except OSError as e:
                    if self._running:
                        logger.error(f"Accept error: {e}")
                    break

                try:
                    self._handle_client(client_socket)
                except Exception as e:
                    logger.error(f"Error handling client: {e}\n{traceback.format_exc()}")
                finally:
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

    def _read_message(self, sock: socket.socket) -> Optional[Dict[str, Any]]:
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
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

    def _recv_exact(self, sock: socket.socket, size: int) -> Optional[bytes]:
        data = bytearray()
        while len(data) < size:
            try:
                chunk = sock.recv(min(SOCKET_BUFFER_SIZE, size - len(data)))
            except socket.timeout:
                continue
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _send_message(self, sock: socket.socket, message: Dict[str, Any]) -> None:
        body = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
        if len(body) > MAX_MESSAGE_SIZE:
            raise ValueError(f"Response too large: {len(body)} > {MAX_MESSAGE_SIZE}")
        sock.sendall(struct.pack(">I", len(body)) + body)

    def _process_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        request_id = message.get("id", str(uuid.uuid4()))

        cached = self._recent_responses.get(request_id)
        if cached is not None:
            logger.warning(f"Duplicate request id {request_id} — replaying cached response")
            return cached

        response = self._execute_message(message, request_id)

        self._recent_responses[request_id] = response
        while len(self._recent_responses) > RECENT_RESPONSES_MAX:
            self._recent_responses.popitem(last=False)
        return response

    def _execute_message(self, message: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        start_time = time.time()
        command_type = message.get("type")
        params = message.get("params", {})
        response: Dict[str, Any] = {"id": request_id}

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
            duration_ms = (time.time() - start_time) * 1000
            response["status"] = "error"
            response["error"] = str(e)
            response["error_type"] = "ValidationError"
            if e.param:
                response["param"] = e.param
            self._operation_logger.log_error(
                command_type or "unknown", params, str(e), "ValidationError", duration_ms
            )

        except LiveAPIError as e:
            duration_ms = (time.time() - start_time) * 1000
            response["status"] = "error"
            response["error"] = str(e)
            response["error_type"] = "LiveAPIError"
            self._operation_logger.log_error(
                command_type or "unknown", params, str(e), "LiveAPIError", duration_ms
            )

        except MainThreadExecutionError as e:
            duration_ms = (time.time() - start_time) * 1000
            response["status"] = "error"
            response["error"] = str(e)
            response["error_type"] = "MainThreadExecutionError"
            response["timeout"] = e.timeout
            self._operation_logger.log_error(
                command_type or "unknown",
                params,
                str(e),
                "MainThreadExecutionError",
                duration_ms,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            tb = traceback.format_exc()
            response["status"] = "error"
            response["error"] = str(e)
            response["error_type"] = type(e).__name__
            logger.error(f"Command error: {e}\n{tb}")
            self._operation_logger.log_error(
                command_type or "unknown",
                params,
                str(e),
                type(e).__name__,
                duration_ms,
                stack_trace=tb,
            )

        return response
