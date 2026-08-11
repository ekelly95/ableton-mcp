"""Marshal command execution onto Live's main thread.

Live's API is not thread-safe; the socket server runs on a daemon thread.
Every command handler is executed here as a single scheduled task on the main
thread (via ControlSurface.schedule_message) while the socket thread blocks on
a response queue with a timeout. One marshal per request — handlers never
marshal internally.

schedule_message rides Live's ~100ms timer tick, so each request carries that
latency floor regardless of payload; that is why the command surface is
batch-first rather than chatty.

Timeout contract: the scheduled task refuses to START once its deadline
(request time + timeout) has passed, and the waiter holds on for an extra
grace window beyond the deadline. So a timeout error means "never executed and
never will" — except for one residual race, documented at the late-delivery
branch below, which is journaled rather than lost.
"""

import queue
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import COMMAND_TIMEOUTS, DEFAULT_TIMEOUT_SECONDS, MARSHAL_GRACE_SECONDS
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
    expired: bool = False


class ThreadMarshaler:
    def __init__(
        self,
        control_surface: Any,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        grace: float | None = None,
        operation_logger: Any = None,
    ):
        self._control_surface = control_surface
        self._default_timeout = default_timeout
        self._grace = MARSHAL_GRACE_SECONDS if grace is None else grace
        self._operation_logger = operation_logger

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
        command_name = command or func_name
        response_queue: queue.Queue[ExecutionResult] = queue.Queue()
        deadline = time.monotonic() + effective_timeout
        # state_lock guarantees exactly one of {waiter consumes the outcome,
        # task journals it}: the task checks `abandoned` under the lock before
        # putting, and the waiter sets it under the lock after its final drain.
        state_lock = threading.Lock()
        abandoned = [False]

        def journal(kind: str, detail: str) -> None:
            if self._operation_logger is None:
                return
            try:
                self._operation_logger.log_marshal_event(kind, command_name, detail)
            except Exception:
                pass

        def task():
            now = time.monotonic()
            if now >= deadline:
                # Refuse to start: this is what makes a timeout response mean
                # "the Set was not modified".
                detail = f"arrived {now - deadline:.1f}s past its deadline; handler NOT run"
                with state_lock:
                    if abandoned[0]:
                        journal("expired", detail)
                    else:
                        response_queue.put(
                            ExecutionResult(
                                success=False,
                                error=detail,
                                error_type="Expired",
                                expired=True,
                            )
                        )
                return
            try:
                result = func(*args, **kwargs)
                outcome = ExecutionResult(success=True, result=result)
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"Main thread execution error in {func_name}: {e}\n{tb}")
                outcome = ExecutionResult(
                    success=False,
                    error=str(e),
                    error_type=type(e).__name__,
                    traceback=tb,
                    exception=e,
                )
            with state_lock:
                if abandoned[0]:
                    # Residual race: the task STARTED before its deadline but
                    # finished after the waiter gave up (timeout + grace). The
                    # Set may have changed even though the client saw a
                    # timeout error — journal it, since the response path can
                    # no longer report it.
                    if outcome.success:
                        journal("late_success", "finished after the waiter abandoned the request")
                    else:
                        journal("late_error", f"failed late: {outcome.error}")
                    return
                response_queue.put(outcome)

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

        execution_result: ExecutionResult | None
        try:
            execution_result = response_queue.get(timeout=effective_timeout + self._grace)
        except queue.Empty:
            with state_lock:
                # Final drain under the lock: the task may have delivered in
                # the instant between queue.get timing out and us locking.
                try:
                    execution_result = response_queue.get_nowait()
                except queue.Empty:
                    abandoned[0] = True
                    execution_result = None
            if execution_result is None:
                raise MainThreadExecutionError(
                    f"Not executed within {effective_timeout}s (+{self._grace}s grace) — "
                    f"Live's main thread never ran the task, and it will refuse to run it "
                    f"late. The Set was NOT modified by this request.",
                    function_name=func_name,
                    timeout=True,
                ) from None

        if execution_result.expired:
            raise MainThreadExecutionError(
                f"Deadline ({effective_timeout}s) passed before Live's main thread picked "
                f"the task up; it refused to run. The Set was NOT modified by this request.",
                function_name=func_name,
                timeout=True,
            )

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
