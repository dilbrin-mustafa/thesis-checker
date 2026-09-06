"""DAG shape tests (doc 03 §4-§6) — only two hard deps exist."""

from __future__ import annotations

from app.orchestrator.pipeline import PIPELINE, topological_groups


def test_topological_groups_cover_all_stages() -> None:
    groups = topological_groups(PIPELINE)
    names = [s.name for g in groups for s in g]
    assert sorted(names) == sorted(s.name for s in PIPELINE)


def test_only_enrich_and_embed_are_hard_deps() -> None:
    by_name = {s.name: s for s in PIPELINE}
    # Everything except parse needs enrich transitively; similarity needs embed.
    assert by_name["similarity"].depends_on == ("embed",)
    assert by_name["embed"].depends_on == ("enrich",)
    for name in ("format", "grammar", "aigt", "citations", "embed"):
        assert by_name[name].depends_on == ("enrich",), name


def test_only_three_fatal_stages() -> None:
    fatals = {s.name for s in PIPELINE if s.fatal}
    assert fatals == {"parse", "enrich", "aggregate"}


def test_consent_gated_stages() -> None:
    by_name = {s.name: s for s in PIPELINE}
    assert by_name["citations"].requires_consent == "external_metadata"
    assert by_name["similarity"].requires_consent == "external_web"
    assert by_name["format"].requires_consent is None
