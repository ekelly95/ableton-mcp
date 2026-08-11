"""Logging for the AbletonMCP control surface.

Two channels:
- get_logger(): rotating file log plus a stderr echo — stderr reaches Live's
  Log.txt, and stdout stays untouched (it belongs to MCP stdio when this
  package is imported by the server process).
- OperationLogger: one JSONL line per executed command (params, result, timing)
  so a session can be replayed and debugged after the fact.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import (
    CONTROL_SURFACE_NAME,
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_FILE_MAX_SIZE,
    VERSION,
)

_logger_cache: dict[str, logging.Logger] = {}


def _ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _LivePrintHandler(logging.Handler):
    """Console logging via stderr, never stdout.

    stderr on purpose: this module is also imported by the MCP server process
    (registry import chain), where any stdout write corrupts the MCP stdio
    transport. Inside Live, stderr reaches Log.txt like stdout does (VERIFY at
    P4 checkpoint; the rotating file log is the primary channel regardless).
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            print(f"[{CONTROL_SURFACE_NAME}] {self.format(record)}", file=sys.stderr)
        except Exception:
            pass


def get_logger(name: str) -> logging.Logger:
    if name in _logger_cache:
        return _logger_cache[name]

    logger = logging.getLogger(f"{CONTROL_SURFACE_NAME}.{name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    try:
        from logging.handlers import RotatingFileHandler

        log_file = _ensure_log_dir() / f"{CONTROL_SURFACE_NAME.lower()}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=LOG_FILE_MAX_SIZE,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[{CONTROL_SURFACE_NAME}] Warning: file logging unavailable: {e}", file=sys.stderr)

    print_handler = _LivePrintHandler()
    print_handler.setLevel(logging.INFO)
    print_handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
    logger.addHandler(print_handler)

    _logger_cache[name] = logger
    return logger


class OperationLogger:
    """JSONL journal of command executions."""

    def __init__(self, log_name: str = "operations"):
        self._logger = get_logger(f"OperationLogger.{log_name}")
        try:
            self._log_file: Path | None = _ensure_log_dir() / f"{log_name}.jsonl"
        except Exception as e:
            self._logger.error(f"Operation log unavailable: {e}")
            self._log_file = None

    @staticmethod
    def _sanitize(data: Any, max_length: int = 1000) -> Any:
        if data is None or isinstance(data, (bool, int, float)):
            return data
        if isinstance(data, str):
            if len(data) > max_length:
                return f"{data[:max_length]}... [truncated, total length: {len(data)}]"
            return data
        if isinstance(data, bytes):
            return f"<bytes, length: {len(data)}>"
        if isinstance(data, (list, tuple)):
            if len(data) > 100:
                out = [OperationLogger._sanitize(v, max_length) for v in data[:100]]
                out.append(f"... [{len(data) - 100} more items]")
                return out
            return [OperationLogger._sanitize(v, max_length) for v in data]
        if isinstance(data, dict):
            if len(data) > 50:
                keys = list(data.keys())[:50]
                out = {k: OperationLogger._sanitize(data[k], max_length) for k in keys}
                out["__truncated__"] = f"{len(data) - 50} more keys"
                return out
            return {k: OperationLogger._sanitize(v, max_length) for k, v in data.items()}
        try:
            text = str(data)
            return text if len(text) <= max_length else f"{text[:max_length]}... [truncated]"
        except Exception:
            return f"<unserializable: {type(data).__name__}>"

    def _rotate_if_needed(self) -> None:
        if self._log_file is None or not self._log_file.exists():
            return
        try:
            if self._log_file.stat().st_size >= LOG_FILE_MAX_SIZE:
                for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
                    old = self._log_file.with_suffix(f".jsonl.{i}")
                    new = self._log_file.with_suffix(f".jsonl.{i + 1}")
                    if old.exists():
                        if new.exists():
                            new.unlink()
                        old.rename(new)
                backup = self._log_file.with_suffix(".jsonl.1")
                if backup.exists():
                    backup.unlink()
                self._log_file.rename(backup)
                self._log_file.touch()
        except Exception as e:
            self._logger.error(f"Log rotation failed: {e}")

    def _write_entry(self, entry: dict[str, Any]) -> None:
        if self._log_file is None:
            return
        try:
            self._rotate_if_needed()
            with open(self._log_file, "a", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, default=str)
                f.write("\n")
        except Exception as e:
            self._logger.error(f"Failed to write operation log entry: {e}")

    def log(self, command: str, params: Any, result: Any, duration_ms: float) -> None:
        self._write_entry(
            {
                "timestamp": _utc_now_iso(),
                "type": "success",
                "command": command,
                "params": self._sanitize(params),
                "result": self._sanitize(result),
                "duration_ms": round(duration_ms, 2),
                "version": VERSION,
            }
        )

    def log_marshal_event(self, kind: str, command: str, detail: str) -> None:
        """Journal marshal-lifecycle outcomes the request/response path never
        sees: kind is 'expired' (task refused to start past its deadline),
        'late_success' or 'late_error' (task finished after the waiter
        abandoned it). Without these, a timed-out command that executed anyway
        would be invisible in the journal."""
        self._write_entry(
            {
                "timestamp": _utc_now_iso(),
                "type": kind,
                "command": command,
                "detail": self._sanitize(detail),
                "version": VERSION,
            }
        )
        self._logger.warning(f"Marshal {kind} for '{command}': {detail}")

    def log_error(
        self,
        command: str,
        params: Any,
        error: str,
        error_type: str,
        duration_ms: float,
        stack_trace: str | None = None,
    ) -> None:
        entry = {
            "timestamp": _utc_now_iso(),
            "type": "error",
            "command": command,
            "params": self._sanitize(params),
            "error": self._sanitize(error),
            "error_type": error_type,
            "duration_ms": round(duration_ms, 2),
            "version": VERSION,
        }
        if stack_trace:
            entry["stack_trace"] = self._sanitize(stack_trace, max_length=2000)
        self._write_entry(entry)
        self._logger.error(f"Command '{command}' failed after {duration_ms:.2f}ms: {error}")


_operation_logger: OperationLogger | None = None


def get_operation_logger() -> OperationLogger:
    global _operation_logger
    if _operation_logger is None:
        _operation_logger = OperationLogger()
    return _operation_logger
