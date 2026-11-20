"""Arrange editable family-tree cards by their confirmed generations."""

from __future__ import annotations

import heapq
from collections import defaultdict, deque


LEFT = 42
TOP = 110
COLUMN_STEP = 210
GENERATION_STEP = 136


def _reachable(graph, source, destination):
    pending = [source]
    visited = {source}
    while pending:
        for child in graph[pending.pop()]:
            if child == destination:
                return True
            if child not in visited:
                visited.add(child)
                pending.append(child)
    return False


def _generations(graph):
    incoming = dict.fromkeys(graph, 0)
    for children in graph.values():
        for child in children:
            incoming[child] += 1
    pending = deque(person for person in graph if incoming[person] == 0)
    generations = dict.fromkeys(graph, 0)
    visited = 0
    while pending:
        person = pending.popleft()
        visited += 1
        for child in graph[person]:
            generations[child] = max(generations[child], generations[person] + 1)
            incoming[child] -= 1
            if incoming[child] == 0:
                pending.append(child)
    if visited != len(graph):
        raise ValueError("已确认的代际关系存在循环。请核对亲子、收养或继亲的方向后再出图。")
    return generations


def _merge_peers(graph, peers, person_order):
    groups = {person: [person] for person in graph}
    group_of = {person: person for person in graph}
    for a, b in peers:
        a, b = group_of[a], group_of[b]
        if a == b or _reachable(graph, a, b) or _reachable(graph, b, a):
            continue
        if person_order[a] > person_order[b]:
            a, b = b, a
        members = groups.pop(b)
        groups[a].extend(members)
        for person in members:
            group_of[person] = a

        merged_children = graph.pop(b)
        graph[a].update(merged_children)
        for children in graph.values():
            if b in children:
                children.remove(b)
                children.add(a)
    return group_of


def _components(graph):
    neighbors = {group: set(children) for group, children in graph.items()}
    for parent, children in graph.items():
        for child in children:
            neighbors[child].add(parent)

    visited = set()
    for seed in graph:
        if seed in visited:
            continue
        component = {seed}
        pending = [seed]
        while pending:
            current = pending.pop()
            new = neighbors[current] - component
            component.update(new)
            pending.extend(new)
        visited.update(component)
        yield component


def _explicit_sibling_edges(parents, sibling_order, generations, person_order):
    branches = defaultdict(list)
    for parent, child in parents:
        rank = sibling_order.get((parent, child))
        if rank is not None:
            branches[parent, generations[child]].append((rank, person_order[child], child))

    row_edges = defaultdict(list)
    for (_, generation), ranked in branches.items():
        children = [child for _, _, child in sorted(ranked)]
        row_edges[generation].extend(zip(children, children[1:]))
    return row_edges


def _order_row(row, key, sibling_edges):
    graph = {person: set() for person in row}
    incoming = dict.fromkeys(graph, 0)
    for older, younger in sibling_edges:
        if older != younger and older in graph and younger in graph and younger not in graph[older]:
            if not _reachable(graph, younger, older):
                graph[older].add(younger)
                incoming[younger] += 1

    available = [(key(person), person) for person in row if incoming[person] == 0]
    heapq.heapify(available)
    while available:
        _, person = heapq.heappop(available)
        yield person
        for younger in graph[person]:
            incoming[younger] -= 1
            if incoming[younger] == 0:
                heapq.heappush(available, (key(younger), younger))


def _fit_to_parents(anchors, left):
    """Keep row order and card spacing while following parents' positions."""
    # Offsets after removing the minimum card spacing must be nondecreasing.
    # Average conflicting neighbors so close branches spread in both directions.
    blocks = []
    for index, anchor in enumerate(anchors):
        total = anchor - index * COLUMN_STEP
        count = 1
        while blocks and blocks[-1][0] / blocks[-1][1] > total / count:
            previous_total, previous_count = blocks.pop()
            total += previous_total
            count += previous_count
        blocks.append((total, count))

    positions = []
    for total, count in blocks:
        offset = max(left, round(total / count))
        for _ in range(count):
            positions.append(offset + len(positions) * COLUMN_STEP)
    return positions


def layout_people(
    people: list[str],
    parents: list[tuple[str, str]],
    peers: list[tuple[str, str]],
    sibling_order: dict[tuple[str, str], int],
) -> dict[str, tuple[int, int]]:
    """Return the top-left positions of people in the editable drawing."""
    person_order = {person: index for index, person in enumerate(people)}
    graph = {person: set() for person in people}
    parents = list(dict.fromkeys(parents))
    for parent, child in parents:
        graph[parent].add(child)
    _generations(graph)

    group_of = _merge_peers(graph, peers, person_order)
    group_generation = _generations(graph)
    generations = {person: group_generation[group_of[person]] for person in people}
    sibling_edges = _explicit_sibling_edges(parents, sibling_order, generations, person_order)

    group_people = defaultdict(list)
    for person in people:
        group_people[group_of[person]].append(person)
    group_parents = defaultdict(set)
    for parent, child in parents:
        group_parents[group_of[child]].add(parent)

    positions = {}
    left = LEFT
    for component in _components(graph):
        rows = defaultdict(list)
        for group in component:
            rows[group_generation[group]].extend(group_people[group])
        span = max(map(len, rows.values()))
        rightmost = left
        for generation, row in sorted(rows.items()):
            anchors = {}
            row_groups = {group_of[person] for person in row}
            for group in row_groups:
                parent_x = [positions[parent][0] for parent in group_parents[group]]
                anchors[group] = sum(parent_x) / len(parent_x) if parent_x else 0

            row_sibling_edges = sibling_edges[generation]
            sibling_group_edges = [(group_of[older], group_of[younger]) for older, younger in row_sibling_edges]
            group_slots = {
                group: index for index, group in enumerate(
                    _order_row(row_groups, lambda group: (anchors[group], person_order[group]), sibling_group_edges)
                )
            }

            def key(person):
                return group_slots[group_of[person]], person_order[person]

            ordered = list(_order_row(row, key, row_sibling_edges))
            if generation == 0:
                start = left + (span - len(row)) * COLUMN_STEP // 2
                row_positions = [start + index * COLUMN_STEP for index in range(len(ordered))]
            else:
                row_positions = _fit_to_parents((anchors[group_of[person]] for person in ordered), left)
            y = TOP + generation * GENERATION_STEP
            for person, x in zip(ordered, row_positions):
                positions[person] = (x, y)
            rightmost = max(rightmost, row_positions[-1])
        left = rightmost + COLUMN_STEP + COLUMN_STEP // 2
    return positions
