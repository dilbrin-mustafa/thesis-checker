"""Pipeline DAG skeleton (doc 03 §6). Full runner lands in Phase 2.

Phase 0: stage declarations + topological grouping only — no execution.
This lets us test DAG shape (only ENRICH + EMBED are hard deps) early.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str
    depends_on: tuple[str, ...] = ()
    timeout_s: int = 120
    fatal: bool = False
    requires_consent: str | None = None


PIPELINE: list[Stage] = [
    Stage("parse", timeout_s=120, fatal=True),
    Stage("enrich", ("parse",), timeout_s=60, fatal=True),
    Stage("format", ("enrich",), timeout_s=30),
    Stage("grammar", ("enrich",), timeout_s=300),
    Stage("aigt", ("enrich",), timeout_s=180),
    Stage("citations", ("enrich",), timeout_s=300, requires_consent="external_metadata"),
    Stage("embed", ("enrich",), timeout_s=120),
    Stage("similarity", ("embed",), timeout_s=600, requires_consent="external_web"),
    Stage(
        "aggregate",
        ("format", "grammar", "aigt", "citations", "similarity"),
        timeout_s=10,
        fatal=True,
    ),
    Stage("report", ("aggregate",), timeout_s=30),
]


def topological_groups(stages: list[Stage]) -> list[list[Stage]]:
    """Kahn's algorithm, grouped by depth. Raises ValueError on cycles."""
    remaining: dict[str, Stage] = {s.name: s for s in stages}
    done: set[str] = set()
    groups: list[list[Stage]] = []
    while remaining:
        ready = [s for s in remaining.values() if set(s.depends_on) <= done]
        if not ready:
            raise ValueError(f"Cycle or missing dependency in: {sorted(remaining)}")
        groups.append(sorted(ready, key=lambda s: s.name))
        for s in ready:
            done.add(s.name)
            del remaining[s.name]
    return groups


FATAL_STAGES: set[str] = {s.name for s in PIPELINE if s.fatal}
assert {"parse", "enrich", "aggregate"} == FATAL_STAGES, FATAL_STAGES
