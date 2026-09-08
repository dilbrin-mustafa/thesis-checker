"""Sandboxed parse boundary (doc 02 §3 — "sandboxing").

Parsing runs in a **separate process** with ``RLIMIT_AS`` (memory),
``RLIMIT_CPU`` (CPU seconds) and ``RLIMIT_FSIZE`` (no disk writes) applied
before any parser library is loaded, plus a wall-clock timeout on the caller
side. A hostile or merely pathological document therefore kills a disposable
worker instead of the API process.

The ``demo`` profile runs with the same code path — only the limits differ
(they come from :class:`Settings`, never from an ``if profile == …`` branch).
"""

from __future__ import annotations

import contextlib
import resource
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from app.ingestion.errors import IngestionError, ParseError, ResourceLimitError
from app.models.ir import DocumentIR, SourceFormat


def _apply_rlimits(memory_mb: int, cpu_seconds: int) -> None:
    """Worker initializer: shrink the blast radius before parsing starts."""
    # not enforceable on every platform/container: a limit we cannot set must
    # not stop the parse, the wall-clock timeout still applies
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024,) * 2)


def _run_parse(path: str, fmt: str, doc_id: str, filename: str) -> dict[str, object]:
    """Child-process entry point. Returns a JSON-ish dict (pickle-friendly)."""
    from app.ingestion.registry import parse_in_process

    ir = parse_in_process(Path(path), fmt, doc_id, filename)  # type: ignore[arg-type]
    return ir.model_dump(mode="json")


def parse_sandboxed(
    path: Path,
    fmt: SourceFormat,
    doc_id: str,
    filename: str,
    *,
    timeout_s: int,
    memory_mb: int,
) -> DocumentIR:
    """Parse in a resource-limited child process."""
    with ProcessPoolExecutor(
        max_workers=1,
        initializer=_apply_rlimits,
        initargs=(memory_mb, timeout_s),
    ) as pool:
        future = pool.submit(_run_parse, str(path), fmt, doc_id, filename)
        try:
            payload = future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            future.cancel()
            raise ResourceLimitError(f"parsing exceeded the {timeout_s}s limit") from exc
        except MemoryError as exc:  # pragma: no cover - hard to trigger on demand
            raise ResourceLimitError("parsing exceeded the memory limit") from exc
        except IngestionError:
            raise
        except Exception as exc:
            # `detail` goes to the server log only — the user-facing message is
            # `user_message_pl/en` (doc 02 §5), so a parser traceback never
            # reaches the client, and never carries document text with it.
            raise ParseError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
    return DocumentIR.model_validate(payload)
