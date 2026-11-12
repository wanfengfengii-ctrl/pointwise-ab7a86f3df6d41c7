"""Functional semantic cases judged by the independent oracle.

Each case drives the production Store through the public API and checks the
observed outcome family against qa.oracle (never against merge.py's own
solver). Case IDs are stable so REPORT.md and evidence.json can reference
them. The contract-mandated sample scenarios live here too, alongside the
trickier same-value/missing/delete/intermediate-equivalence cases.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merge import Error, Store
from samples import AMBIGUOUS, DELETE, NORMAL, OBSERVED
from qa.oracle import judge


def _rows_set(outcomes):
    return {tuple(tuple(r) for r in o["rows"]) for o in outcomes}


def run_case(case_id, batches, expect_error=None, memory=True,
             extra_checks=None):
    """Apply batches to a fresh store and judge the final family.

    batches: list of (batch_id, expected_generation, events) triples.
    """
    store = Store(":memory:" if memory else None)
    receipts = []
    observed_error = None
    try:
        for batch_id, gen, events in batches:
            receipts.append(store.apply(batch_id, gen, events))
        read = store.read()
        # Build the full accepted event set for the oracle.
        accepted = {e["id"]: e for e in store.audit()["events"]}
        verdict = judge(accepted, read["outcomes"])
        family_ok = verdict["exact"]
        witness_ok = all(w["legal"] and w["matches_rows"]
                         for w in verdict["witness_checks"])
    except Error as exc:
        observed_error = str(exc)
        read = None
        family_ok = witness_ok = False
        verdict = {}
    finally:
        store.close()
    if expect_error is not None:
        passed = observed_error == expect_error
    else:
        passed = observed_error is None and family_ok and witness_ok
        if extra_checks is not None:
            passed = passed and extra_checks(receipts, read)
    return {
        "id": case_id,
        "passed": bool(passed),
        "observed_error": observed_error,
        "expected_error": expect_error,
        "family_exact": family_ok,
        "witnesses_ok": witness_ok,
        "verdict": verdict,
        "receipts": receipts,
        "read": read,
    }


def all_cases():
    cases = []

    # F-01: contract NORMAL sample, unique ledger, ordered witness.
    cases.append(("F01-normal-unique", [
        ("normal", 0, NORMAL),
    ], None, lambda receipts, read: (
        receipts[0] == {"batch_id": "normal", "generation": 1,
                        "events": 2, "outcomes": 1}
        and _rows_set(read["outcomes"]) == {
            (("well-a", 12), ("well-b", 20))})))

    # F-02: AMBIGUOUS family, no cross-well splicing.
    cases.append(("F02-ambiguous-no-splice", [
        ("two-agents", 0, AMBIGUOUS),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("well-a", 10), ("well-b", 20)),
        (("well-a", 11), ("well-b", 19)),
    }))

    # F-03: DELETE after AMBIGUOUS; family shrinks, history immutable.
    def f03(receipts, read):
        if read["generation"] != 2:
            return False
        return _rows_set(read["outcomes"]) == {
            (("well-b", 19),), (("well-b", 20),)}
    cases.append(("F03-delete-family-shrink", [
        ("two-agents", 0, AMBIGUOUS),
        ("withdraw", 1, DELETE),
    ], None, f03))

    # F-04: OBSERVED read evidence selects exactly one branch (11/19).
    cases.append(("F04-observed-read-selects-branch", [
        ("two-agents", 0, AMBIGUOUS),
        ("observed", 1, OBSERVED),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("well-a", 11), ("well-b", 19)),
    }))

    # F-05: same value from different sources; distinct origins still one
    # ledger, read must be jointly satisfiable by a complete order.
    cases.append(("F05-same-value-different-source", [
        ("b", 0, [
            {"id": "a", "parents": [], "reads": {}, "writes": {"k": 1}},
            {"id": "b", "parents": [], "reads": {}, "writes": {"k": 1}},
            {"id": "r", "parents": [], "reads": {"k": 1}, "writes": {}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("k", 1),)}))

    # F-06: null read satisfied by either initial empty state or a delete;
    # both joint interpretations must appear when each is feasible.
    cases.append(("F06-null-read-two-origins", [
        ("b", 0, [
            {"id": "w", "parents": [], "reads": {}, "writes": {"k": 5}},
            {"id": "d", "parents": [], "reads": {}, "writes": {"k": None}},
            {"id": "r", "parents": [], "reads": {"k": None}, "writes": {}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (), (("k", 5),)}))

    # F-07: zero preserved through a self read-write event.
    cases.append(("F07-zero-and-self-read-write", [
        ("b", 0, [
            {"id": "a", "parents": [], "reads": {"k": None},
             "writes": {"k": 0}},
            {"id": "b", "parents": ["a"], "reads": {"k": 0},
             "writes": {"k": 0}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("k", 0),)}))

    # F-08: missing-value read forces the delete before the reader; the final
    # family still contains both ledgers reachable by complete legal orders.
    cases.append(("F08-missing-read-after-delete", [
        ("b", 0, [
            {"id": "w", "parents": [], "reads": {}, "writes": {"k": 7}},
            {"id": "d", "parents": [], "reads": {}, "writes": {"k": None}},
            {"id": "r", "parents": ["d"], "reads": {"k": None},
             "writes": {}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (), (("k", 7),)}))

    # F-09: read-only / empty-write barrier keeps causal role.
    cases.append(("F09-empty-write-barrier", [
        ("b", 0, [
            {"id": "barrier", "parents": [], "reads": {}, "writes": {}},
            {"id": "after", "parents": ["barrier"], "reads": {},
             "writes": {"k": 3}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("k", 3),)}))

    # F-10: infeasible read -> whole batch rejected, no empty family.
    cases.append(("F10-infeasible-batch-rejected", [
        ("good", 0, [
            {"id": "w", "parents": [], "reads": {}, "writes": {"k": 1}},
        ]),
        ("bad", 1, [
            {"id": "r", "parents": [], "reads": {"k": 2}, "writes": {}},
        ]),
    ], "INCONSISTENT", None))

    # F-11: cross-batch parent reference resolved across batches.
    cases.append(("F11-cross-batch-parent", [
        ("first", 0, [
            {"id": "root", "parents": [], "reads": {},
             "writes": {"k": 1}},
        ]),
        ("second", 1, [
            {"id": "child", "parents": ["root"], "reads": {"k": 1},
             "writes": {"k": 2}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("k", 2),)}))

    # F-12: identical intermediate ledger (k=1 from either writer a or b) but
    # different remaining event sets reach different final ledgers: a witness
    # ending with b rewrites k back to 1, while one ending with final leaves 9.
    # The solver must track the consumed ideal, not merge equal visible maps.
    cases.append(("F12-equal-interval-distinct-future", [
        ("b", 0, [
            {"id": "a", "parents": [], "reads": {}, "writes": {"k": 1}},
            {"id": "b", "parents": [], "reads": {}, "writes": {"k": 1}},
            {"id": "guard", "parents": [], "reads": {"k": 1},
             "writes": {}},
            {"id": "final", "parents": ["guard"], "reads": {"k": 1},
             "writes": {"k": 9}},
        ]),
    ], None, lambda receipts, read: _rows_set(read["outcomes"]) == {
        (("k", 1),), (("k", 9),)}))

    # F-13: delete of a nonexistent well is legal.
    cases.append(("F13-delete-nonexistent-well", [
        ("b", 0, [
            {"id": "d", "parents": [], "reads": {},
             "writes": {"ghost": None}},
        ]),
    ], None, lambda receipts, read: (
        _rows_set(read["outcomes"]) == {()}
        and receipts[0]["outcomes"] == 1)))

    # F-14: empty batch still advances the generation and keeps the family.
    def f14(receipts, read):
        return (receipts[0]["generation"] == 1
                and receipts[0]["events"] == 0
                and receipts[0]["outcomes"] == 1
                and read["generation"] == 1
                and read["outcomes"] == [{"rows": [], "order": []}])
    cases.append(("F14-empty-batch-advances-generation", [
        ("empty", 0, []),
    ], None, f14))

    return cases


def run_all():
    results = []
    for case_id, batches, expect_error, extra in all_cases():
        result = run_case(case_id, batches, expect_error=expect_error,
                          extra_checks=extra)
        results.append(result)
    return results
