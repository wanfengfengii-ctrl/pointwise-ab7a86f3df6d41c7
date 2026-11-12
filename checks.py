import json
import sys
from merge import Error, Store
from samples import AMBIGUOUS, DELETE, NORMAL, OBSERVED


def normal():
    store = Store(":memory:")
    assert store.read() == {"generation": 0, "outcomes": [{"rows": [], "order": []}]}
    receipt = store.apply("normal", 0, NORMAL)
    assert receipt == {"batch_id": "normal", "generation": 1, "events": 2, "outcomes": 1}
    assert store.read()["outcomes"] == [{
        "rows": [["well-a", 12], ["well-b", 20]],
        "order": ["capture", "correction"]}]
    assert store.apply("normal", 0, list(reversed(NORMAL))) == receipt
    before = store.audit()
    def fail(stage):
        if stage == "after_snapshot":
            raise RuntimeError("interrupted")
    try:
        store.apply("withdraw", 1, [
            {"id": "delete", "parents": ["correction"], "reads": {}, "writes": {"well-a": None}}
        ], fault=fail)
    except RuntimeError as exc:
        assert str(exc) == "interrupted"
    else:
        raise AssertionError("injection did not run")
    assert store.audit() == before
    try:
        store.apply("bad", 1, [{"id": "lost", "parents": ["missing"], "reads": {}, "writes": {}}])
    except Error as exc:
        assert str(exc) == "MISSING_PARENT"
    else:
        raise AssertionError("missing parent accepted")
    assert store.audit() == before
    store.close()
    print("NORMAL_REGRESSION_OK")


def demo():
    store = Store(":memory:")
    first = store.apply("two-agents", 0, AMBIGUOUS)
    print(json.dumps({"receipt": first, "read": store.read()}, ensure_ascii=False))
    second = store.apply("withdraw", 1, DELETE)
    print(json.dumps({"receipt": second, "read": store.read()}, ensure_ascii=False))
    replay = store.apply("two-agents", 0, AMBIGUOUS)
    store.apply("observed", 2, [
        {"id": "read-after-delete", "parents": ["withdraw"],
         "reads": {"well-a": None, "well-b": 19}, "writes": {}}
    ])
    print(json.dumps({"observed": store.read()}, ensure_ascii=False))
    print(json.dumps({"replayed_receipt": replay, "head": store.read()["generation"],
                      "historical": store.read(1)}, ensure_ascii=False))
    store.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"normal", "demo"}:
        raise SystemExit("usage: python3 checks.py normal|demo")
    {"normal": normal, "demo": demo}[sys.argv[1]]()
