"""Executable mutation harness for the independent oracle.

Mutations are applied only to an isolated in-memory copy of merge.py loaded
from source text; the production file on disk is never modified. Each mutant
embodies one of the three failure classes CONTRACT names:

  * causal-loss:     drop a parent edge, relaxing execution ordering;
  * cross-well-splice: combine per-well outcomes across distinct executions;
  * receipt/state-split: make the receipt outcome count disagree with the
                     persisted snapshot family.

The harness proves the oracle (or the receipt cross-check) detects every
mutant, then shrinks a triggering input and records expected vs actual.
"""
import copy
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merge import Error, Store as BaselineStore
from qa.oracle import judge
from qa.randomize import evaluate, generate_graph, shrink
import random


def _load_mutated_merge(source_mutator, module_name):
    """Load a fresh merge module from mutated source, isolated from baseline."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "merge.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    mutated = source_mutator(source)
    module = types.ModuleType(module_name)
    module.__file__ = path
    exec(compile(mutated, path, "exec"), module.__dict__)
    return module


def mutate_causal_loss():
    import re
    def mutator(source):
        # Neutralize the parent-edge contribution inside the solver's need
        # mask so the mutated store ignores causal ordering constraints.
        pattern = re.compile(
            r'need = \{name: sum\(1 << index\[p\] for p in events\[name\]'
            r'\["parents"\]\)\s*\n\s*for name in names\}')
        mutated, count = pattern.subn(
            "need = {name: 0 for name in names}", source)
        assert count == 1, f"causal-loss anchor count={count}"
        return mutated
    return _load_mutated_merge(mutator, "merge_mut_causal")


def mutate_cross_well_splice():
    def mutator(source):
        # Replace the final outcome family with a per-well Cartesian
        # combination, which can splice values from distinct legal executions.
        helper = (
            "\n\ndef _qa_splice(outcomes):\n"
            "    import itertools as _it\n"
            "    per_well = {}\n"
            "    for outcome in outcomes:\n"
            "        for key, value in outcome['rows']:\n"
            "            per_well.setdefault(key, set()).add(value)\n"
            "    if not per_well:\n"
            "        return outcomes\n"
            "    keys = sorted(per_well)\n"
            "    combos = _it.product(*[sorted(per_well[k]) for k in keys])\n"
            "    return [{'rows': [[k, v] for k, v in zip(keys, combo)],\n"
            "             'order': outcomes[0]['order']} for combo in combos]\n")
        old = 'return sorted(outcomes, key=lambda outcome: outcome["rows"])'
        new = 'return sorted(_qa_splice(outcomes), key=lambda o: o["rows"])'
        assert old in source, "splice anchor not found"
        return source.replace(old, new, 1) + helper
    return _load_mutated_merge(mutator, "merge_mut_splice")


def mutate_receipt_split():
    def mutator(source):
        # Inflate the receipt's outcome count so it diverges from the real
        # persisted snapshot family (receipt/state split).
        old = '"events": len(current), "outcomes": len(outcomes)}'
        new = '"events": len(current), "outcomes": len(outcomes) + 1}'
        assert old in source, "receipt-split anchor not found"
        return source.replace(old, new)
    return _load_mutated_merge(mutator, "merge_mut_receipt")


def evaluate_with_module(module, events):
    store = module.Store(":memory:")
    try:
        receipt = store.apply("mut", 0, copy.deepcopy(events))
    except module.Error as exc:
        store.close()
        return False, {"error": str(exc)}
    read = store.read()
    accepted = {e["id"]: e for e in store.audit()["events"]}
    verdict = judge(accepted, read["outcomes"])
    store.close()
    family_bad = not verdict["exact"] or any(
        not (w["legal"] and w["matches_rows"])
        for w in verdict["witness_checks"])
    receipt_bad = receipt["outcomes"] != len(read["outcomes"])
    return (family_bad or receipt_bad), {
        "family_exact": verdict["exact"],
        "witness_ok": all(w["legal"] and w["matches_rows"]
                          for w in verdict["witness_checks"]),
        "receipt_outcomes": receipt["outcomes"],
        "actual_outcomes": len(read["outcomes"]),
        "spurious": verdict.get("spurious"),
        "missing": verdict.get("missing"),
        "receipt_split": receipt_bad}


def find_trigger(module, rng, attempts=60):
    for _ in range(attempts):
        events = generate_graph(rng, rng.randint(3, 8))
        detected, observed = evaluate_with_module(module, events)
        if detected:
            return events, observed
    return None, None


def shrink_for_module(module, events, rng):
    """Shrink while the mutated module still trips a contract check."""
    def eval_mut(evs):
        detected, _ = evaluate_with_module(module, evs)
        return not detected  # "passes" means undetected
    current = [dict(e) for e in events]
    changed = True
    while changed and len(current) > 1:
        changed = False
        for index in range(len(current)):
            candidate = [dict(e) for e in current]
            removed = candidate.pop(index)
            for event in candidate:
                event["parents"] = [p for p in event["parents"]
                                    if p != removed["id"]]
            if eval_mut(candidate) is False:  # still detected
                current = candidate
                changed = True
                break
    return current


def run_all(seed_dir=None):
    rng = random.Random(90917)
    mutants = [
        ("M01-causal-loss-detected", mutate_causal_loss, "causal_loss"),
        ("M02-cross-well-splice-detected", mutate_cross_well_splice,
         "cross_well_splice"),
        ("M03-receipt-state-split-detected", mutate_receipt_split,
         "receipt_state_split"),
    ]
    results = []
    for case_id, loader, kind in mutants:
        module = loader()
        trigger, observed = find_trigger(module, rng)
        if trigger is None:
            results.append({"id": case_id, "kind": kind, "passed": False,
                            "detail": "no triggering input found"})
            continue
        shrunk = shrink_for_module(module, trigger, rng)
        _, shrunk_observed = evaluate_with_module(module, shrunk)
        # Sanity: the baseline (unmutated) must pass the same shrunk input.
        baseline_passed, _ = evaluate(copy.deepcopy(shrunk))
        result = {
            "id": case_id,
            "kind": kind,
            "passed": bool(not shrunk_observed.get("family_exact", True)
                           or not shrunk_observed.get("witness_ok", True)
                           or shrunk_observed.get("receipt_split", False)),
            "baseline_passes_shrunk_input": baseline_passed,
            "trigger_size": len(trigger),
            "shrunk_size": len(shrunk),
            "shrunk_input": shrunk,
            "expected": "oracle flags missing/spurious ledger, illegal "
                        "witness, or receipt/snapshot count divergence",
            "actual": shrunk_observed,
        }
        results.append(result)
        if seed_dir:
            with open(os.path.join(seed_dir, f"{case_id}.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2,
                          sort_keys=True)
    return results
