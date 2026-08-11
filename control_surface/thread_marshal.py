"""Marshal command execution onto Live's main thread.

Live's API is not thread-safe; the socket server runs on a daemon thread.
Every command handler is executed here as a single scheduled task on the main
thread (via ControlSurface.schedule_message) while the socket thread blocks on
a response queue with a timeout. One marshal per request — handlers never
marshal internally.

schedule_message rides Live's ~100ms timer tick, so each request carries that
latency floor regardless of payload; that is why the command surface is
batch-first rather than chatty.
"""

import queue
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import COMMAND_TIMEOUTS, DEFAULT_TIMEOUT_SECONDS
from .log import get_logger

logger = get_logger("thread_marshal")


class MainThreadExecutionError(Exception):
    def __init__(
        self,
        message: str,
        function_name: Optional[str] = None,
        timeout: bool = False,
        original_error: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.message = message
        self.function_name = function_name
        self.timeout = timeout
        self.original_error = original_error

    def __str__(self) -> str:
        parts = [self.message]
        if self.function_name:
            parts.append(f"function: {self.function_name}")
        if self.timeout:
            parts.append("(timeout)")
        return " | ".join(parts)


@dataclass
class ExecutionResult:
    success: bool
    result: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    traceback: Optional[str] = None


class ThreadMarshaler:
    def __init__(self, control_surface: Any, default_timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._control_surface = control_surface
        self._default_timeout = default_timeout

    def execute(
        self,
        func: Callable,
        *args: Any,
        timeout: Optional[float] = None,
        command: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        effective_timeout = timeout
        if effective_timeout is None and command:
            effective_timeout = COMMAND_TIMEOUTS.get(command)
        if effective_timeout is None:
            effective_timeout = self._default_timeout

        func_name = getattr(func, "__name__", str(func))
        response_queue: "queue.Queue[ExecutionResult]" = queue.Queue()

        def task():
            try:
                result = func(*args, **kwargs)
                response_queue.put(ExecutionResult(success=True, result=result))
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"Main thread execution error in {func_name}: {e}\n{tb}")
                response_queue.put(
                    ExecutionResult(
                        success=False,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=tb,
                    )
                )

        try:
            self._control_surface.schedule_message(0, task)
        except Exception as e:
            raise MainThreadExecutionError(
                f"Failed to schedule task: {e}",
                function_name=func_name,
                original_error=e,
            ) from e

        try:
            execution_result = response_queue.get(timeout=effective_timeout)
        except queue.Empty:
            raise MainThreadExecutionError(
                f"Execution timed out after {effective_timeout}s",
                function_name=func_name,
                timeout=True,
            ) from None

        if execution_result.success:
            return execution_result.result

        # Re-raise typed errors so the socket server's taxonomy survives the
        # thread hop: a ValidationError/LiveAPIError raised by a handler on the
        # main thread must not be flattened into a generic execution error.
        from .registry import LiveAPIError, ValidationError

        if execution_result.error_type == "LiveAPIError":
            raise LiveAPIError(execution_result.error or "Live API error")
        if execution_result.error_type == "ValidationError":
            raise ValidationError(execution_result.error or "Validation error")

        raise MainThreadExecutionError(
            execution_result.error or "Unknown execution error",
            function_name=func_name,
        )
