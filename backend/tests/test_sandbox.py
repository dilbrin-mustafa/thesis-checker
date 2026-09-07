"""Sandboxed-parse tests (doc 02 §3 "sandboxing").

A hostile document must be able to kill a disposable worker — never the service.
These tests exercise the real subprocess path, not a mock of it.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from app.ingestion import sandbox as sandbox_module
from app.ingestion.errors import ParseError, ResourceLimitError
from app.ingestion.registry import parse_document
from app.ingestion.sandbox import _apply_rlimits, parse_sandboxed
from tests.conftest import CorpusEntry


def _read_limits() -> tuple[int, int, int]:
    """Read the limits back from inside a worker (module-level: picklable)."""
    import resource

    return (
        resource.getrlimit(resource.RLIMIT_AS)[0],
        resource.getrlimit(resource.RLIMIT_CPU)[0],
        resource.getrlimit(resource.RLIMIT_FSIZE)[0],
    )


def test_rlimits_are_applied_in_the_worker() -> None:
    """The initializer is the whole control — verify it landed in the child.

    Deliberately *not* applied in the test process: that would put a 512 MB
    address space and a CPU cap on pytest itself.
    """
    with ProcessPoolExecutor(max_workers=1, initializer=_apply_rlimits, initargs=(512, 30)) as pool:
        address_space, cpu_seconds, file_size = pool.submit(_read_limits).result(timeout=60)
    assert address_space == 512 * 1024 * 1024
    assert cpu_seconds == 30
    assert file_size == 64 * 1024 * 1024


def test_sandboxed_parse_returns_the_same_ir(corpus: dict[str, CorpusEntry]) -> None:
    path = corpus["docx"]["path"]
    inside = parse_document(path, sandbox=False)
    outside = parse_sandboxed(
        path, "docx", "doc-sandbox", "thesis.docx", timeout_s=120, memory_mb=2048
    )
    assert outside.plain_text == inside.plain_text
    assert [b.id for b in outside.blocks] == [b.id for b in inside.blocks]
    assert [b.type for b in outside.blocks] == [b.type for b in inside.blocks]


def _slow_parse(*args: object) -> dict[str, object]:
    """Module-level (so it is picklable) stand-in for a hanging parser."""
    time.sleep(30)
    raise AssertionError("unreachable")


def test_wall_clock_timeout_raises_resource_limit(
    corpus: dict[str, CorpusEntry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parser that hangs must surface as a limit error, not a hung request."""
    monkeypatch.setattr(sandbox_module, "_run_parse", _slow_parse)
    path = corpus["docx"]["path"]
    with pytest.raises(ResourceLimitError):
        parse_sandboxed(path, "docx", "doc-timeout", "thesis.docx", timeout_s=1, memory_mb=2048)


def test_child_exception_becomes_a_parse_error(tmp_path: Path) -> None:
    """Errors cross the process boundary as ParseError, with a loggable detail."""
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04" + b"\0" * 64)
    with pytest.raises(ParseError) as excinfo:
        parse_sandboxed(path, "docx", "doc-broken", "broken.docx", timeout_s=60, memory_mb=2048)
    assert excinfo.value.user_message_en  # a user-facing message exists
    assert excinfo.value.detail  # and a server-side detail for the log


def test_sandbox_is_on_by_default() -> None:
    from app.core.config import get_settings

    assert get_settings().parse_sandboxed is True
