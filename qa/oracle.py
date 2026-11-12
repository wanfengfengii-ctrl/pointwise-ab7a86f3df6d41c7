"""Independent semantic oracle for the merge contract.

Two exact decision procedures, neither of which imports, calls or
reproduces merge.py's normalization, connected-component grouping or
ideal-frontier DP:

1. ``enumerate_outcomes``: DFS over *all* topological orders with direct
   state simulation. Used on small graphs and as a cross-check against the
   SAT procedure.
2. ``sat_outcomes``: a home-grown DPLL SAT encoding (qa.sat) that decides,
   per candidate final ledger, whether some complete legal topological order
   realizes it. It never enumerates topological orders and scales to coupled
   large graphs where full enumeration is infeasible.

Every observed witness is additionally checked by direct simulation.
"""
from .sat import Solver, lit, neg


_MISSING = object()


class TooManyOrders(Exception):
    def __init__(self, count):
        self.count = count


def simulate(events, order):
    """Execute one complete order; return sorted rows tuple or None."""
    state = {}
    for name in order:
        event = events[name]
        for key, value in event["reads"].items():
            if state.get(key) != value:
                return None
        for key, value in event["writes"].items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
    return tuple(sorted(state.items()))


def legal_witness(events, order):
    if sorted(order) != sorted(events) or len(order) != len(events):
        return False
    position = {name: i for i, name in enumerate(order)}
    for name, event in events.items():
        for parent in event["parents"]:
            if position[parent] >= position[name]:
                return False
    return simulate(events, order) is not None


def enumerate_outcomes(events, order_limit=None):
    """All legal topological orders -> distinct final rows, exhaustively."""
    names = sorted(events)
    remaining = {name: set(events[name]["parents"]) for name in names}
    results = {}
    state = {}
    used = set()
    order = []
    counter = {"orders": 0}

    def dfs():
        if len(order) == len(names):
            counter["orders"] += 1
            if order_limit is not None and counter["orders"] > order_limit:
                raise TooManyOrders(counter["orders"])
            results.setdefault(tuple(sorted(state.items())), tuple(order))
            return
        for name in names:
            if name in used or remaining[name]:
                continue
            event = events[name]
            if any(state.get(key) != value
                   for key, value in event["reads"].items()):
                continue
            changes = []
            for key, value in event["writes"].items():
                changes.append((key, state.get(key, _MISSING)))
                if value is None:
                    state.pop(key, None)
                else:
                    state[key] = value
            used.add(name)
            order.append(name)
            for child in names:
                remaining[child].discard(name)
            dfs()
            for child in names:
                if name in events[child]["parents"]:
                    remaining[child].add(name)
            order.pop()
            used.discard(name)
            for key, old in changes:
                if old is _MISSING:
                    state.pop(key, None)
                else:
                    state[key] = old

    dfs()
    return results, counter["orders"]


def count_topo_orders(events, state_limit=2_000_000):
    """Count topological orders (reads ignored) via reachable-mask DP."""
    names = sorted(events)
    index = {name: i for i, name in enumerate(names)}
    need = {name: 0 for name in names}
    for name in names:
        for parent in events[name]["parents"]:
            if parent not in events:
                raise ValueError("MISSING_PARENT")
            need[name] |= 1 << index[parent]
    full = (1 << len(names)) - 1
    memo = {}

    def ways(mask):
        if mask in memo:
            return memo[mask]
        if mask == full:
            return 1
        total = 0
        for name in names:
            bit = 1 << index[name]
            if not (mask & bit) and (mask & need[name]) == need[name]:
                total += ways(mask | bit)
                if total > state_limit:
                    raise TooManyOrders(total)
        memo[mask] = total
        return total

    return ways(0)


class _SatProblem:
    """SAT encoding of "there exists a legal complete topological order".

    Variables:
      P(pos, name): event name sits at position pos (bijection).
      V(pos, well, value): well holds domain value just before position pos;
      position n is the final ledger. Values in {None} U every written/read
      value of that well.
      W(pos, name): event at pos is a writer of its well; defined only for
      events writing that well, elsewhere unused.
    """

    def __init__(self, events):
        self.events = events
        self.names = sorted(events)
        self.n = len(self.names)
        self.index = {name: i for i, name in enumerate(self.names)}
        wells = sorted({key for event in events.values()
                        for key in set(event["reads"]) | set(event["writes"])})
        self.wells = wells
        self.domains = {}
        for key in wells:
            values = {None}
            for event in events.values():
                if key in event["writes"]:
                    values.add(event["writes"][key])
                if key in event["reads"]:
                    values.add(event["reads"][key])
            self.domains[key] = sorted(
                values, key=lambda v: (-1 if v is None else v))
        self.n_pos = self.n * self.n
        cursor = self.n_pos
        self.val_base = cursor
        self.val_size = {key: (self.n + 1) * len(self.domains[key])
                         for key in wells}
        cursor += sum(self.val_size.values())
        # W is per (position, well, writer), so an event that writes several
        # wells is not mistaken for a writer of wells it never touches.
        self.writers = {key: [name for name in self.names
                              if key in events[name]["writes"]]
                        for key in wells}
        self.w_size = sum(self.n * len(self.writers[key]) for key in wells)
        self.w_base = cursor
        self.total_vars = cursor + self.w_size
        self.w_offset = {}
        wcursor = cursor
        for key in wells:
            self.w_offset[key] = wcursor
            wcursor += self.n * len(self.writers[key])

    def p_var(self, pos, name):
        return pos * self.n + self.index[name]

    def v_var(self, pos, key, value):
        offset = 0
        for other in self.wells:
            if other == key:
                break
            offset += self.val_size[other]
        return (self.val_base + offset
                + pos * len(self.domains[key])
                + self.domains[key].index(value))

    def w_var(self, pos, key, name):
        return (self.w_offset[key]
                + pos * len(self.writers[key])
                + self.writers[key].index(name))

    def build(self, target_rows=None):
        solver = Solver(self.total_vars)
        n = self.n
        for pos in range(n):
            solver.add_exactly_one(
                [lit(self.p_var(pos, name), True) for name in self.names])
        for name in self.names:
            solver.add_exactly_one(
                [lit(self.p_var(pos, name), True) for pos in range(n)])
        # Parent edges: if child occupies pos, its parent occupies some
        # strictly earlier position q < pos. Clause form:
        # not P(child,pos) OR OR_{q<pos} P(parent,q).
        for name in self.names:
            for parent in self.events[name]["parents"]:
                for pos in range(n):
                    solver.add_clause(
                        [neg(lit(self.p_var(pos, name), True))]
                        + [lit(self.p_var(q, parent), True)
                           for q in range(0, pos)])
        # Well values: exactly one domain value per position, initial None.
        for key in self.wells:
            for pos in range(n + 1):
                solver.add_exactly_one(
                    [lit(self.v_var(pos, key, value), True)
                     for value in self.domains[key]])
            solver.add_clause([lit(self.v_var(0, key, None), True)])
        for key in self.wells:
            writers = self.writers[key]
            for pos in range(n):
                if not writers:
                    for value in self.domains[key]:
                        now = lit(self.v_var(pos, key, value), True)
                        nxt = lit(self.v_var(pos + 1, key, value), True)
                        solver.add_clause([neg(now), nxt])
                        solver.add_clause([neg(nxt), now])
                    continue
                for name in writers:
                    p = lit(self.p_var(pos, name), True)
                    w = lit(self.w_var(pos, key, name), True)
                    solver.add_clause([neg(p), w])
                    solver.add_clause([neg(w), p])
                for value in self.domains[key]:
                    x_next = lit(self.v_var(pos + 1, key, value), True)
                    x_now = lit(self.v_var(pos, key, value), True)
                    writers_of_value = [
                        lit(self.w_var(pos, key, name), True)
                        for name in writers
                        if self.events[name]["writes"][key] == value]
                    # next=value => (some writer writes value) OR
                    # (no writer fires AND current=value). Correct CNF of
                    # x -> B v (C1 & ... & Ck & D) needs D in its own clause.
                    for name in writers:
                        solver.add_clause(
                            [neg(x_next)] + writers_of_value
                            + [neg(lit(self.w_var(pos, key, name), True))])
                    solver.add_clause(
                        [neg(x_next)] + writers_of_value + [x_now])
        # Read preconditions at the event's position.
        for name in self.names:
            for key, value in self.events[name]["reads"].items():
                for pos in range(n):
                    solver.add_clause([
                        neg(lit(self.p_var(pos, name), True)),
                        lit(self.v_var(pos, key, value), True)])
        if target_rows is not None:
            wanted = dict(target_rows)
            for key in self.wells:
                solver.add_clause([lit(
                    self.v_var(n, key, wanted.get(key, None)), True)])
        return solver

    def model_order(self, model):
        # Solver convention: values[v] == 1 means the positive literal is true.
        order = []
        for pos in range(self.n):
            for name in self.names:
                if model[self.p_var(pos, name)] == 1:
                    order.append(name)
                    break
        if len(order) != self.n:
            raise AssertionError("oracle model missing position assignment")
        return order

    def block_clause(self, rows_tuple):
        wanted = dict(rows_tuple)
        return [neg(lit(self.v_var(self.n, key, wanted.get(key, None)),
                        True))
                for key in self.wells]


def sat_outcomes(events):
    """Exact distinct final ledgers via All-SAT; never lists topo orders."""
    if not events:
        return {(): ()}
    problem = _SatProblem(events)
    # With no observed wells every legal order yields the identical empty
    # ledger, so a single satisfying model is the whole outcome family.
    if not problem.wells:
        solver = problem.build(target_rows=None)
        model = solver.solve()
        if model is None:
            return {}
        order = tuple(problem.model_order(model))
        if simulate(events, list(order)) is None:
            raise AssertionError("oracle SAT model produced an illegal order")
        return {(): order}
    # One solver instance is reused across the All-SAT loop: blocking
    # clauses are indexed incrementally, avoiding a full rebuild each round.
    solver = problem.build(target_rows=None)
    found = {}
    while True:
        model = solver.solve()
        if model is None:
            break
        order = tuple(problem.model_order(model))
        rows = simulate(events, list(order))
        if rows is None:
            raise AssertionError("oracle SAT model produced an illegal order")
        if rows in found:
            raise AssertionError("oracle repeated an outcome")
        found[rows] = order
        block = problem.block_clause(rows)
        if block:
            solver.add_clause(block)
    return found


def judge(events, actual_outcomes, enumerate_limit=200_000):
    """Independently decide whether an observed outcome family is exact."""
    names = sorted(events)
    for name in names:
        for parent in events[name]["parents"]:
            if parent not in events:
                raise ValueError("MISSING_PARENT")
    try:
        topo_count = count_topo_orders(events)
        countable = True
    except TooManyOrders as exc:
        topo_count = exc.count
        countable = False
    enum_feasible = countable and topo_count <= enumerate_limit
    legal_orders = None
    if enum_feasible:
        enum_results, legal_orders = enumerate_outcomes(events)
        method = "enumerate+sat"
    else:
        enum_results = None
        method = "sat"
    sat_results = sat_outcomes(events)
    if enum_feasible and set(enum_results) != set(sat_results):
        raise AssertionError("oracle internal disagreement between "
                             "enumeration and SAT")
    expected = set(sat_results)
    actual_rows = {tuple(tuple(row) for row in outcome["rows"])
                   for outcome in actual_outcomes}
    witness_checks = []
    for outcome in actual_outcomes:
        order = list(outcome["order"])
        rows = tuple(tuple(row) for row in outcome["rows"])
        witness_checks.append({
            "order": order,
            "rows": [list(row) for row in rows],
            "legal": legal_witness(events, order),
            "matches_rows": simulate(events, order) == rows,
        })
    return {
        "method": method,
        "topo_order_count": topo_count if countable else None,
        "topo_order_lower_bound": None if countable else topo_count,
        "enumeration_feasible": enum_feasible,
        "legal_order_count": legal_orders,
        "expected_rows": [[list(row) for row in rows]
                          for rows in sorted(sat_results)],
        "actual_rows": [[list(row) for row in rows]
                        for rows in sorted(actual_rows)],
        "exact": expected == actual_rows,
        "missing": [[list(row) for row in rows]
                    for rows in sorted(expected - actual_rows)],
        "spurious": [[list(row) for row in rows]
                     for rows in sorted(actual_rows - expected)],
        "witness_checks": witness_checks,
    }
