"""The heap-based dynamic frontier order matches the sort-every-step reference exactly.

`_reference_order` is the list-sorting implementation the heap replaced, kept here as the
oracle: same ready set, same priority key, so the two must agree on every graph, ties
included.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from arete.application.queue.builder import _dynamic_frontier_order, _weakness_score
from arete.application.queue.graph_resolver import topological_sort
from arete.domain.graph import CardNode, DependencyGraph


def _reference_order(  # noqa: C901 -- the reference is kept as written
    graph: DependencyGraph,
    candidate_ids: list[str],
    due_set: set[str],
    card_stats: dict[str, dict] | None,
) -> list[str]:
    if not candidate_ids:
        return []

    valid = [cid for cid in candidate_ids if cid in graph.nodes]
    valid_set = set(valid)

    in_degree: dict[str, int] = dict.fromkeys(valid, 0)
    dependents: dict[str, list[str]] = {cid: [] for cid in valid}

    for cid in valid:
        for prereq_id in graph.get_prerequisites(cid):
            if prereq_id in valid_set:
                in_degree[cid] += 1
                dependents[prereq_id].append(cid)

    probe_degree = dict(in_degree)
    probe_ready = [cid for cid in valid if probe_degree[cid] == 0]
    seen = 0
    while probe_ready:
        node = probe_ready.pop()
        seen += 1
        for dep in dependents[node]:
            probe_degree[dep] -= 1
            if probe_degree[dep] == 0:
                probe_ready.append(dep)
    if seen != len(valid):
        return topological_sort(graph, valid)

    topo_order: list[str] = []
    topo_degree = dict(in_degree)
    topo_ready = sorted([cid for cid in valid if topo_degree[cid] == 0])
    while topo_ready:
        node = topo_ready.pop(0)
        topo_order.append(node)
        for dep in sorted(dependents[node]):
            topo_degree[dep] -= 1
            if topo_degree[dep] == 0:
                topo_ready.append(dep)
        topo_ready.sort()

    due_reach: dict[str, set[str]] = {cid: set() for cid in valid}
    for node in reversed(topo_order):
        reach = {node} if node in due_set else set()
        for dep in dependents[node]:
            reach.update(due_reach[dep])
        due_reach[node] = reach

    remaining = dict(in_degree)
    ready = [cid for cid in valid if remaining[cid] == 0]
    ordered: list[str] = []

    def score(card_id: str) -> float:
        unlock_score = float(len(due_reach[card_id]))
        weak_score = _weakness_score(card_id, card_stats)
        prereq_bonus = 0.5 if (card_id not in due_set and unlock_score > 0) else 0.0
        due_bonus = 0.25 if card_id in due_set else 0.0
        return (2.0 * unlock_score) + weak_score + prereq_bonus + due_bonus

    while ready:
        ready.sort(key=lambda cid: (-score(cid), cid))
        node = ready.pop(0)
        ordered.append(node)

        for dep in dependents[node]:
            remaining[dep] -= 1
            if remaining[dep] == 0:
                ready.append(dep)

    return ordered


# Short ids from a small alphabet, so lexical ties and shared prefixes are common.
_ids = st.lists(st.text("abc", min_size=1, max_size=3), min_size=0, max_size=14, unique=True)
_stat_value = st.one_of(
    st.none(),
    st.integers(min_value=-5, max_value=30),
    st.floats(min_value=-5, max_value=1e6, allow_nan=False),
    st.just(float("inf")),
    st.text(max_size=2),
)
_stats = st.dictionaries(
    st.sampled_from(["stability", "lapses", "reps", "interval"]), _stat_value, max_size=4
)


@st.composite
def _case(draw, acyclic: bool):
    ids = draw(_ids)
    graph = DependencyGraph()
    for cid in ids:
        graph.add_node(CardNode(id=cid, title=cid, file_path="t.md", line_number=1))
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if i == j or (acyclic and j >= i):
                continue
            if draw(st.integers(0, 3)) == 0:
                graph.add_requires(a, b)
    # Candidates: a subset in any order, with repeats and ids the graph does not hold.
    pool = ids + ["zz", "ghost"]
    candidates = draw(st.lists(st.sampled_from(pool), max_size=18)) if pool else []
    due = set(draw(st.lists(st.sampled_from(pool), max_size=8))) if pool else set()
    card_stats = draw(
        st.one_of(st.none(), st.dictionaries(st.sampled_from(pool), _stats, max_size=8))
    )
    return graph, candidates, due, card_stats


@given(case=_case(acyclic=True))
@settings(max_examples=400, deadline=None)
def test_heap_order_matches_reference_on_dags(case):
    graph, candidates, due, card_stats = case
    assert _dynamic_frontier_order(graph, candidates, due, card_stats) == _reference_order(
        graph, candidates, due, card_stats
    )


@given(case=_case(acyclic=False))
@settings(max_examples=200, deadline=None)
def test_heap_order_matches_reference_with_cycles(case):
    graph, candidates, due, card_stats = case
    assert _dynamic_frontier_order(graph, candidates, due, card_stats) == _reference_order(
        graph, candidates, due, card_stats
    )


def test_ties_break_on_id():
    """Equal scores: the lexically smaller id comes first."""
    graph = DependencyGraph()
    for cid in ["b", "a", "c"]:
        graph.add_node(CardNode(id=cid, title=cid, file_path="t.md", line_number=1))
    assert _dynamic_frontier_order(graph, ["c", "b", "a"], {"a", "b", "c"}, None) == [
        "a",
        "b",
        "c",
    ]
