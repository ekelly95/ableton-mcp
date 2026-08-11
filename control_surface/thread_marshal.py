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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import COMMAND_TIMEOUTS, DEFAULT_TIMEOUT_SECONDS
from .errors import LiveAPIError, ValidationError
from .log import get_logger

logger = get_logger("thread_marshal")


class MainThreadExecutionError(Exception):
    def __init__(
        self,
        message: str,
        function_name: str | None = None,
        timeout: bool = False,
        original_error: Exception | None = None,
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
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    exception: BaseException | None = None


class ThreadMarshaler:
    def __init__(self, control_surface: Any, default_timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._control_surface = control_surface
        self._default_timeout = default_timeout

    def execute(
        self,
        func: Callable,
        *args: Any,
        timeout: float | None = None,
        command: str | None = None,
        **kwargs: Any,
    ) -> Any:
        effective_timeout = timeout
        if effective_timeout is None and command:
            effective_timeout = COMMAND_TIMEOUTS.get(command)
        if effective_timeout is None:
            effective_timeout = self._default_timeout

        func_name = getattr(func, "__name__", str(func))
        response_queue: queue.Queue[ExecutionResult] = queue.Queue()

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
                        exception=e,
                    )
                )

        try:
            # Delay of 1 tick (~100ms), not 0: some _Framework versions assert
            # delay_in_ticks > 0, and 1 lands on the same next pump anyway.
            self._control_surface.schedule_message(1, task)
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
        # thread hop. The ORIGINAL exception object is re-raised (not rebuilt
        # from its string) so subclasses like PartialApplyError keep their
        # structured attributes (.applied) across the hop.
        if isinstance(execution_result.exception, (LiveAPIError, ValidationError)):
            raise execution_result.exception

        raise MainThreadExecutionError(
            execution_result.error or "Unknown execution error",
            function_name=func_name,
        )
