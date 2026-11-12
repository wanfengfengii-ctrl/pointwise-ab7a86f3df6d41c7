"""Random property testing with seed retention and automatic shrinking.

Generates random single-batch event graphs, runs the production Store and
judges the result family with the independent oracle. On any semantic
deviation the input is shrunk (remove events, trim mappings) to a minimal
reproducer; the seed and full rerun input are persisted so the failure can be
replayed deterministically.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merge import Error, Store
from qa.oracle import judge, legal_witness, simulate


def generate_graph(rng, n):
    ids = [f"e{i}" for i in range(n)]
    n_wells = max(1, n // 2 + 1)
    events = []
    for i, name in enumerate(ids):
        pool = ids[:i]
        parents = sorted(rng.sample(pool, min(len(pool), rng.randint(0, 2))))
        reads = {}
        if rng.random() < 0.5 and i:
            key = f"w{rng.randrange(n_wells)}"
            reads = {key: rng.choice([None] + list(range(5)))}
        writes = {}
        if rng.random() < 0.85:
            keys = rng.sample(range(n_wells),
                              min(n_wells, rng.randint(1, 2)))
            for key_index in keys:
                writes[f"w{key_index}"] = rng.choice(
                    [None] + list(range(5)))
        events.append({"id": name, "parents": parents, "reads": reads,
                       "writes": writes})
    return events


def normalize_for_store(events):
    return [dict(e) for e in events]


def evaluate(events):
    """Return (passed, observed). Reject-only graphs count as passing if the
    store reports INCONSISTENT while the oracle also finds no legal order."""
    store = Store(":memory:")
    try:
        store.apply("rand", 0, normalize_for_store(events))
    except Error as exc:
        marker = str(exc)
        store.close()
        accepted = {e["id"]: e for e in events}
        if marker == "INCONSISTENT":
            from qa.oracle import sat_outcomes
            return (len(sat_outcomes(accepted)) == 0), {
                "error": marker, "oracle_outcomes": 0}
        return False, {"error": marker}
    read = store.read()
    accepted = {e["id"]: e for e in store.audit()["events"]}
    verdict = judge(accepted, read["outcomes"])
    store.close()
    passed = verdict["exact"] and all(
        w["legal"] and w["matches_rows"] for w in verdict["witness_checks"])
    return passed, {"verdict": verdict, "read": read}


def shrink(events, rng):
    """Greedily remove events and mapping entries while the failure persists."""
    current = [dict(e) for e in events]
    changed = True
    while changed and len(current) > 1:
        changed = False
        for index in range(len(current)):
            candidate = [dict(e) for e in current]
            removed = candidate.pop(index)
            # Re-link parent references that pointed at the removed event.
            removed_id = removed["id"]
            for event in candidate:
                event["parents"] = [p for p in event["parents"]
                                    if p != removed_id]
            ok, _ = evaluate(candidate)
            if not ok:
                current = candidate
                changed = True
                break
    # Trim writes/reads mappings entry by entry.
    for field in ("writes", "reads"):
        trimmed = True
        while trimmed:
            trimmed = False
            for event in current:
                keys = list(event[field])
                for key in keys:
                    candidate = [dict(e) for e in current]
                    for ce in candidate:
                        ce[field] = dict(ce[field])
                    next_event = next(ce for ce in candidate
                                      if ce["id"] == event["id"])
                    del next_event[field][key]
                    ok, _ = evaluate(candidate)
                    if not ok:
                        current = candidate
                        trimmed = True
                        break
                if trimmed:
                    break
    return current


def run(seed_count=120, seed_dir=None, base_seed=20260101):
    findings = []
    trials = []
    for index in range(seed_count):
        seed = base_seed + index
        rng = random.Random(seed)
        n = rng.randint(2, 9)
        events = generate_graph(rng, n)
        passed, observed = evaluate(events)
        trials.append({"seed": seed, "n": n, "passed": passed})
        if not passed:
            shrunk = shrink(events, rng)
            shrunk_passed, shrunk_observed = evaluate(shrunk)
            finding = {
                "seed": seed,
                "original_size": len(events),
                "original_events": events,
                "shrunk_size": len(shrunk),
                "shrunk_events": shrunk,
                "shrunk_still_fails": not shrunk_passed,
                "observed": _jsonable(observed),
            }
            findings.append(finding)
            if seed_dir:
                path = os.path.join(seed_dir, f"finding-seed-{seed}.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(finding, handle, ensure_ascii=False, indent=2,
                              sort_keys=True)
    return {
        "trials": trials,
        "passed_trials": sum(1 for t in trials if t["passed"]),
        "findings": findings,
    }


def _jsonable(value):
    try:
        json.dumps(value, allow_nan=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str, ensure_ascii=False))
