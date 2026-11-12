"""From-scratch DPLL SAT solver, stdlib only.

Written solely for the independent quality oracle; it shares no code or
algorithm with merge.py. Variables are non-negative ints; literals are
``2*v`` (positive) and ``2*v + 1`` (negative).

Truth convention: an assignment stores 1 when the variable's positive
literal is true and 0 when it is false; -1 means unassigned.

Implementation: persistent literal occurrence lists (so clauses added for
All-SAT blocking are indexed incrementally), queue-based unit propagation,
and chronological backtracking that undoes exactly one decision level.
The unit/conflict classification counts satisfied and free literals, so
repeated literals and mixed clauses cannot confuse it.
"""


def lit(var, sign):
    return 2 * var + (0 if sign else 1)


def neg(literal):
    return literal ^ 1


class Solver:
    def __init__(self, n_vars):
        self.n_vars = n_vars
        self.clauses = []
        self._occurrences = [[] for _ in range(2 * n_vars)]

    def add_clause(self, literals):
        clause = list(literals)
        cid = len(self.clauses)
        self.clauses.append(clause)
        for literal in set(clause):
            self._occurrences[literal].append(cid)

    def add_exactly_one(self, literals):
        # At-least-one plus a linear at-most-one via sequential auxiliary
        # variables (Sinz counter), avoiding quadratic pairwise clauses.
        self.add_clause(list(literals))
        self.add_at_most_one(literals)

    def add_at_most_one(self, literals):
        # Sequential counter (Sinz 2005). For primary literals x_1..x_k and
        # auxiliary s_1..s_{k-1}: (not x1 or s1); for 2<=i<k,
        # (not s_{i-1} or not x_i), (not x_i or s_i),
        # (not s_{i-1} or s_i); and (not s_{k-1} or not x_k).
        k = len(literals)
        if k <= 1:
            return
        base = self.n_vars
        aux = list(range(base, base + k - 1))
        self.n_vars = base + k - 1
        self._occurrences.extend([] for _ in range(2 * (k - 1)))
        self.add_clause([neg(literals[0]), lit(aux[0], True)])
        for i in range(1, k - 1):
            prev = aux[i - 1]
            here = aux[i]
            self.add_clause([neg(lit(prev, True)), neg(literals[i])])
            self.add_clause([neg(literals[i]), lit(here, True)])
            self.add_clause([neg(lit(prev, True)), lit(here, True)])
        self.add_clause([neg(lit(aux[-1], True)), neg(literals[-1])])

    def solve(self, assumptions=()):
        n_vars = self.n_vars
        occurrences = self._occurrences
        values = [-1] * n_vars
        trail = []            # all assigned literals in assignment order
        level_end = []        # trail length after each decision level
        queue_head = [0]

        def is_true(literal):
            return values[literal // 2] == (1 if literal % 2 == 0 else 0)

        def assign(literal):
            var = literal // 2
            wanted = 1 if literal % 2 == 0 else 0
            current = values[var]
            if current == wanted:
                return True
            if current != -1:
                return False
            values[var] = wanted
            trail.append(literal)
            return True

        def undo_level():
            marker = level_end.pop()
            while len(trail) > marker:
                values[trail.pop() // 2] = -1
            queue_head[0] = marker

        def propagate():
            while queue_head[0] < len(trail):
                literal = trail[queue_head[0]]
                queue_head[0] += 1
                # Only clauses containing the newly false literal can turn
                # into unit or conflict clauses.
                for cid in occurrences[neg(literal)]:
                    satisfied = False
                    free = []
                    for candidate in self.clauses[cid]:
                        if is_true(candidate):
                            satisfied = True
                            break
                        if values[candidate // 2] == -1:
                            free.append(candidate)
                    if satisfied:
                        continue
                    if not free:
                        return False
                    if len(free) == 1 and not assign(free[0]):
                        return False
            return True

        for assumption in assumptions:
            if not assign(assumption):
                return None
        if not propagate():
            return None

        while True:
            if not propagate():
                # Conflict: keep unwinding decision levels until the flipped
                # decision propagates cleanly, or report UNSAT.
                while True:
                    if not level_end:
                        return None
                    marker = level_end[-1]
                    decision = trail[marker]
                    undo_level()
                    if assign(neg(decision)) and propagate():
                        break
                continue
            chosen = next((v for v in range(n_vars) if values[v] == -1), None)
            if chosen is None:
                return list(values)
            level_end.append(len(trail))
            if not assign(lit(chosen, True)):
                return None
