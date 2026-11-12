"""Transactional merge of causally ordered, atomic collection events."""
import itertools
import json
import sqlite3


class Error(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _normalize(events):
    if not isinstance(events, list):
        raise Error("INVALID")
    result = {}
    for event in events:
        if not isinstance(event, dict) or set(event) != {"id", "parents", "reads", "writes"}:
            raise Error("INVALID")
        name, parents = event["id"], event["parents"]
        reads, writes = event["reads"], event["writes"]
        if not isinstance(name, str) or not name or name in result:
            raise Error("INVALID")
        if (not isinstance(parents, list)
                or any(not isinstance(p, str) or not p for p in parents)
                or len(set(parents)) != len(parents)
                or not isinstance(reads, dict) or not isinstance(writes, dict)):
            raise Error("INVALID")
        for mapping in (reads, writes):
            for key, value in mapping.items():
                if not isinstance(key, str) or not key:
                    raise Error("INVALID")
                if value is not None and (type(value) is not int or value < 0):
                    raise Error("INVALID")
        result[name] = {"id": name, "parents": sorted(parents),
                        "reads": dict(reads), "writes": dict(writes)}
    return [result[name] for name in sorted(result)]


def _components(events):
    # Causal edges and shared accessed keys prevent independent factorization.
    graph = {name: set() for name in events}
    owners = {}
    for name, event in events.items():
        for parent in event["parents"]:
            if parent not in events:
                raise Error("MISSING_PARENT")
            graph[name].add(parent)
            graph[parent].add(name)
        for key in set(event["reads"]) | set(event["writes"]):
            if key in owners:
                other = owners[key]
                graph[name].add(other)
                graph[other].add(name)
            owners[key] = name
    pending = set(events)
    answer = []
    while pending:
        stack = [min(pending)]
        group = set()
        while stack:
            name = stack.pop()
            if name in group:
                continue
            group.add(name)
            stack.extend(graph[name] - group)
        pending -= group
        answer.append(sorted(group))
    return answer


def _check_dag(events):
    indegree = {name: len(e["parents"]) for name, e in events.items()}
    children = {name: [] for name in events}
    for name, e in events.items():
        for parent in e["parents"]:
            if parent not in events:
                raise Error("MISSING_PARENT")
            children[parent].append(name)
    ready = [name for name, count in indegree.items() if count == 0]
    seen = 0
    while ready:
        name = ready.pop()
        seen += 1
        for child in children[name]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if seen != len(events):
        raise Error("CYCLE")


def _solve_component(events, names):
    index = {name: i for i, name in enumerate(names)}
    need = {name: sum(1 << index[p] for p in events[name]["parents"])
            for name in names}
    # A state includes the consumed ideal, not merely its current visible map.
    frontier = {(0, ()): ()}
    for _ in names:
        following = {}
        for (mask, rows), order in frontier.items():
            for name in names:
                bit = 1 << index[name]
                if mask & bit or mask & need[name] != need[name]:
                    continue
                state = dict(rows)
                if any(state.get(key) != value
                       for key, value in events[name]["reads"].items()):
                    continue
                for key, value in events[name]["writes"].items():
                    if value is None:
                        state.pop(key, None)
                    else:
                        state[key] = value
                newrows = tuple(sorted(state.items()))
                following.setdefault((mask | bit, newrows), order + (name,))
        frontier = following
    answer = {}
    for (_, rows), order in frontier.items():
        answer.setdefault(rows, order)
    return [(rows, answer[rows]) for rows in sorted(answer)]


def _solve(events):
    _check_dag(events)
    choices = [_solve_component(events, names) for names in _components(events)]
    outcomes = []
    for combination in itertools.product(*choices):
        rows = sorted(item for part, _ in combination for item in part)
        order = [name for _, part in combination for name in part]
        outcomes.append({"rows": [list(item) for item in rows], "order": order})
    return sorted(outcomes, key=lambda outcome: outcome["rows"])


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(
                generation INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts(
                batch_id TEXT PRIMARY KEY, request TEXT NOT NULL,
                result TEXT NOT NULL);
        """)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES(1,0)")
            self.db.execute("INSERT OR IGNORE INTO snapshots VALUES(0,?)",
                            (_json({"generation": 0, "outcomes": _solve({})}),))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def _receipt(self, batch_id, request):
        row = self.db.execute(
            "SELECT request,result FROM receipts WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            return None
        if row[0] != request:
            raise Error("BATCH_CONFLICT")
        return json.loads(row[1])

    def apply(self, batch_id, expected_generation, events, fault=None):
        if (not isinstance(batch_id, str) or not batch_id
                or type(expected_generation) is not int or expected_generation < 0):
            raise Error("INVALID")
        normalized = _normalize(events)
        request = _json(normalized)
        self.db.execute("BEGIN")
        try:
            receipt = self._receipt(batch_id, request)
            if receipt is not None:
                self.db.commit()
                return receipt
            generation = self.db.execute(
                "SELECT generation FROM meta WHERE singleton=1").fetchone()[0]
            if expected_generation != generation:
                raise Error("GENERATION_CONFLICT")
            current = {name: json.loads(payload) for name, payload in
                       self.db.execute("SELECT id,payload FROM events")}
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        additions = []
        for event in normalized:
            name = event["id"]
            if name in current:
                if current[name] != event:
                    raise Error("EVENT_CONFLICT")
            else:
                current[name] = event
                additions.append(event)
        outcomes = _solve(current)
        if not outcomes:
            raise Error("INCONSISTENT")
        result = {"batch_id": batch_id, "generation": generation + 1,
                  "events": len(current), "outcomes": len(outcomes)}
        snapshot = {"generation": generation + 1, "outcomes": outcomes}
        if fault:
            fault("after_plan")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            receipt = self._receipt(batch_id, request)
            if receipt is not None:
                self.db.commit()
                return receipt
            actual = self.db.execute(
                "SELECT generation FROM meta WHERE singleton=1").fetchone()[0]
            if actual != generation:
                raise Error("GENERATION_CONFLICT")
            for event in additions:
                self.db.execute("INSERT INTO events VALUES(?,?)",
                                (event["id"], _json(event)))
            if fault:
                fault("after_events")
            self.db.execute("INSERT INTO snapshots VALUES(?,?)",
                            (generation + 1, _json(snapshot)))
            if fault:
                fault("after_snapshot")
            self.db.execute("INSERT INTO receipts VALUES(?,?,?)",
                            (batch_id, request, _json(result)))
            self.db.execute("UPDATE meta SET generation=? WHERE singleton=1",
                            (generation + 1,))
            if fault:
                fault("before_commit")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        if fault:
            fault("after_commit")
        return result

    def read(self, generation=None):
        if generation is None:
            row = self.db.execute("""
                SELECT s.payload FROM snapshots s JOIN meta m
                ON s.generation=m.generation WHERE m.singleton=1
            """).fetchone()
        else:
            if type(generation) is not int or generation < 0:
                raise Error("INVALID")
            row = self.db.execute(
                "SELECT payload FROM snapshots WHERE generation=?", (generation,)).fetchone()
        if row is None:
            raise Error("NO_GENERATION")
        return json.loads(row[0])

    def audit(self):
        self.db.execute("BEGIN")
        try:
            answer = {
                "generation": self.db.execute(
                    "SELECT generation FROM meta WHERE singleton=1").fetchone()[0],
                "events": [json.loads(r[0]) for r in self.db.execute(
                    "SELECT payload FROM events ORDER BY id")],
                "receipts": [json.loads(r[0]) for r in self.db.execute(
                    "SELECT result FROM receipts ORDER BY batch_id")],
                "snapshots": [r[0] for r in self.db.execute(
                    "SELECT generation FROM snapshots ORDER BY generation")]
            }
            self.db.commit()
            return answer
        except BaseException:
            self.db.rollback()
            raise
