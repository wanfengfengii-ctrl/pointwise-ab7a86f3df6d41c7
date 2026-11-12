"""Contract error-path and request-identity cases.

These exercise the precedence and identity rules in CONTRACT.md using the
production Store. They do not need the SAT oracle: the expectations are the
documented error markers and receipt semantics.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merge import Error, Store


def expect_error(store, call, marker):
    try:
        call()
    except Error as exc:
        return str(exc) == marker
    return False


def run_all():
    results = []

    def record(case_id, passed, detail):
        results.append({"id": case_id, "passed": bool(passed), "detail": detail})

    # E-01: non-int generation (booleans are not ints) -> INVALID.
    s = Store(":memory:")
    record("E01-bool-generation-invalid",
           expect_error(s, lambda: s.apply("b", True, []), "INVALID"),
           "bool expected_generation rejected")
    record("E02-negative-generation-invalid",
           expect_error(s, lambda: s.apply("b", -1, []), "INVALID"),
           "negative generation rejected")
    record("E03-empty-batch-id-invalid",
           expect_error(s, lambda: s.apply("", 0, []), "INVALID"),
           "empty batch id rejected")

    # E-04: malformed event structure -> INVALID.
    bad_events = [
        [{"id": "x", "parents": [], "reads": {}, "writes": {}, "extra": 1}],
        [{"id": "x", "parents": [], "reads": {}, "writes": -1}],
        [{"id": "x", "parents": [1], "reads": {}, "writes": {}}],
        [{"id": "x", "parents": [], "reads": {"k": -1}, "writes": {}}],
        [{"id": "x", "parents": [], "reads": {}, "writes": {"k": 1.5}}],
        [{"id": "", "parents": [], "reads": {}, "writes": {}}],
        [{"id": "x", "parents": ["x", "x"], "reads": {}, "writes": {}}],
        [{"id": "x", "parents": [], "reads": {}, "writes": {"k": True}}],
        "not-a-list",
    ]
    all_invalid = all(
        expect_error(s, lambda ev=ev: s.apply(f"b{i}", 0, ev), "INVALID")
        for i, ev in enumerate(bad_events))
    record("E04-structural-invalid", all_invalid,
           f"{len(bad_events)} malformed inputs all rejected")

    # E-05: wrong generation on a new batch -> GENERATION_CONFLICT.
    record("E05-generation-conflict",
           expect_error(s, lambda: s.apply("late", 5, []),
                        "GENERATION_CONFLICT"),
           "stale expected generation rejected")

    # E-06: successful empty batch advances the generation.
    receipt = s.apply("g0", 0, [])
    record("E06-empty-batch-ok",
           receipt == {"batch_id": "g0", "generation": 1, "events": 0,
                       "outcomes": 1},
           f"receipt={receipt}")

    # E-07: identical request under the same batch id replays the receipt and
    # does not advance the generation.
    replay = s.apply("g0", 0, [])
    record("E07-same-request-replay",
           replay == receipt and s.read()["generation"] == 1,
           f"replay={replay}")

    # E-08: same batch id, different request -> BATCH_CONFLICT, and it takes
    # precedence over the generation check.
    record("E08-batch-conflict-precedence",
           expect_error(
               s,
               lambda: s.apply("g0", 99, [
                   {"id": "x", "parents": [], "reads": {},
                    "writes": {"k": 1}}]),
               "BATCH_CONFLICT"),
           "batch conflict wins over generation conflict")

    # E-09: reusing an event id with different content -> EVENT_CONFLICT.
    s.apply("ev", 1, [
        {"id": "x", "parents": [], "reads": {}, "writes": {"k": 1}}])
    record("E09-event-conflict",
           expect_error(s, lambda: s.apply("ev2", 2, [
               {"id": "x", "parents": [], "reads": {},
                "writes": {"k": 2}}]), "EVENT_CONFLICT"),
           "changed event under same id rejected")

    # E-10: identical existing event can be re-submitted (no conflict).
    r_same = s.apply("ev3", 2, [
        {"id": "x", "parents": [], "reads": {}, "writes": {"k": 1}}])
    record("E10-identical-event-resubmit",
           r_same["generation"] == 3 and r_same["events"] == 1,
           f"receipt={r_same}")

    # E-11: missing parent -> MISSING_PARENT, precedence over cycle.
    record("E11-missing-parent",
           expect_error(s, lambda: s.apply("mp", 3, [
               {"id": "c", "parents": ["ghost"], "reads": {},
                "writes": {}}]), "MISSING_PARENT"),
           "missing parent reported")

    # E-12: a cycle with all parents present -> CYCLE.
    record("E12-cycle",
           expect_error(s, lambda: s.apply("cy", 3, [
               {"id": "p", "parents": ["q"], "reads": {}, "writes": {}},
               {"id": "q", "parents": ["p"], "reads": {}, "writes": {}}]),
               "CYCLE"),
           "cycle reported")

    # E-13: failed batch leaves no partial state.
    before = s.audit()
    try:
        s.apply("partial", 3, [
            {"id": "ok", "parents": [], "reads": {}, "writes": {"z": 1}},
            {"id": "bad", "parents": ["nope"], "reads": {},
             "writes": {}}])
    except Error:
        pass
    after = s.audit()
    record("E13-failure-atomic-no-partial-state", before == after,
           "audit unchanged after rejected batch")

    # E-14: historical read of an unknown generation -> NO_GENERATION.
    record("E14-no-generation",
           expect_error(s, lambda: s.read(99), "NO_GENERATION"),
           "unknown historical generation rejected")
    record("E15-read-invalid-arg",
           expect_error(s, lambda: s.read(-1), "INVALID"),
           "negative read generation rejected")

    # E-16: request identity ignores input/parent/key ordering.
    payload = [
        {"id": "b", "parents": ["a"], "reads": {"k": 1},
         "writes": {"m": 2}},
        {"id": "a", "parents": [], "reads": {}, "writes": {}},
    ]
    shuffled = [
        {"id": "b", "parents": ["a"], "writes": {"m": 2},
         "reads": {"k": 1}},
        {"id": "a", "writes": {}, "parents": [], "reads": {}},
    ]
    r1 = s.apply("id1", 3, payload)
    r2 = s.apply("id1", 0, shuffled)  # same normalized request -> replay
    record("E16-normalization-identity", r1 == r2,
           "reordered input maps to same request identity")

    s.close()
    return results
