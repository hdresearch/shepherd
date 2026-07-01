"""CI-tracked regression for the projection-shape spike (promoted from
260523-2330 §2330 capability spike per `260524-pre-drive-validation.md`
§"Spike artifact")."""

from __future__ import annotations

from shepherd_kernel_v3_reference.spikes.projection_shapes import (
    PROGRAMS,
    project_program,
    run_shape_spike,
)


def test_projection_shape_corpus_byte_stable() -> None:
    """All five spike programs project byte-identically across three runs."""

    results = run_shape_spike(runs=3)
    assert {r.program_name for r in results} == set(PROGRAMS)
    for result in results:
        assert result.byte_stable_across_runs, (
            f"projection non-deterministic for {result.program_name!r}"
        )


def test_projection_shape_corpus_per_shape_invariants() -> None:
    """Per 2330 §"What Was Confirmed" table — verify the structural shape
    invariants each program must satisfy. Exact entry counts depend on the
    spike's specific program construction; this test pins the *shape*
    invariants that must hold regardless of trivial program differences."""

    for name, builder in PROGRAMS.items():
        batch = project_program(builder())
        ref_kinds = [
            canonical.split(":")[0] for _runtime, canonical in batch.ref_map.entries
        ]
        kind_counts = {k: ref_kinds.count(k) for k in set(ref_kinds)}

        if name == "P1_pure_let":
            assert batch.ref_map.entries == (), (
                f"pure-let must produce empty ref map; got {kind_counts!r}"
            )
            continue

        # Every effect-bearing program has at least one declaration,
        # selection, source, path, and capture.
        for required_kind in ("declaration", "selection", "source", "path", "capture"):
            assert required_kind in kind_counts, (
                f"{name}: missing canonical {required_kind!r}; got {kind_counts!r}"
            )

        # Resume-shape programs additionally produce resume + resume-return.
        if name in ("P2_resume_shape", "P5_nested_resumed"):
            for required_kind in ("resume", "resume-return"):
                assert required_kind in kind_counts, (
                    f"{name}: missing canonical {required_kind!r}; got {kind_counts!r}"
                )

        # Nested programs with outer abort produce at least one closed record.
        if name == "P4_nested_abandoned":
            assert "closed" in kind_counts, (
                f"{name}: outer-abort program must produce selection-closed; "
                f"got {kind_counts!r}"
            )


def test_projection_shape_corpus_cross_program_discrimination() -> None:
    """P2 and P3 both produce a `selection:1` runtime ref but their
    canonical SHAs must differ — the projection is content-sensitive at
    the right granularity (2330 §"What Was Confirmed" #5)."""

    p2 = project_program(PROGRAMS["P2_resume_shape"]())
    p3 = project_program(PROGRAMS["P3_abort_shape"]())

    def _selection_canonical(batch) -> str:
        for runtime, canonical in batch.ref_map.entries:
            if runtime == "selection:1":
                return canonical
        raise AssertionError("selection:1 not in batch ref_map")

    assert _selection_canonical(p2) != _selection_canonical(p3), (
        "selection:1 canonicals must differ across distinct programs"
    )
