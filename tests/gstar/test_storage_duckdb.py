"""DuckDB 스토어 왕복 테스트."""

from __future__ import annotations

from gstar.schema import Cluster, Edge, EmergenceEvent, Goal, Node


def test_node_roundtrip(store):
    n = Node(kind="fact", text="이재원은 다겸의 책임연구원이다", attrs={"source": "memo.md"})
    store.insert_node(n)

    fetched = store.get_node(n.id)
    assert fetched is not None
    assert fetched.id == n.id
    assert fetched.kind == "fact"
    assert fetched.text == n.text
    assert fetched.attrs == {"source": "memo.md"}
    assert fetched.version == 1


def test_list_nodes_by_kind(store):
    store.insert_node(Node(kind="fact", text="A"))
    store.insert_node(Node(kind="fact", text="B"))
    store.insert_node(Node(kind="entity", text="이재원"))

    facts = store.list_nodes(kind="fact")
    entities = store.list_nodes(kind="entity")
    assert len(facts) == 2
    assert len(entities) == 1
    assert store.count_nodes() == 3


def test_edge_and_degree(store):
    a = Node(kind="entity", text="이재원")
    b = Node(kind="entity", text="다겸")
    c = Node(kind="entity", text="OptiREC")
    d = Node(kind="entity", text="RAG")
    for n in (a, b, c, d):
        store.insert_node(n)

    # 이재원 노드에 3개 연결 → 안정, 4번째 → 창발 후보
    store.insert_edge(Edge(src=a.id, dst=b.id, kind="member_of"))
    store.insert_edge(Edge(src=a.id, dst=c.id, kind="authored"))
    store.insert_edge(Edge(src=a.id, dst=d.id, kind="interested_in"))
    assert store.degree(a.id) == 3

    e = Node(kind="entity", text="Claude")
    store.insert_node(e)
    store.insert_edge(Edge(src=a.id, dst=e.id, kind="uses"))
    assert store.degree(a.id) == 4

    edges = store.edges_of(a.id)
    assert len(edges) == 4
    assert {edge.kind for edge in edges} == {"member_of", "authored", "interested_in", "uses"}


def test_goal_roundtrip(store):
    g = Goal(text="OO 사업계획서 작성", kind="proposal")
    store.insert_goal(g)

    fetched = store.get_goal(g.id)
    assert fetched is not None
    assert fetched.text == g.text
    assert fetched.kind == "proposal"

    all_goals = store.list_goals()
    assert len(all_goals) == 1


def test_cluster_and_members(store):
    g = Goal(text="시험 목표", kind="proposal")
    store.insert_goal(g)

    n1 = Node(kind="fact", text="A")
    n2 = Node(kind="fact", text="B")
    store.insert_node(n1)
    store.insert_node(n2)

    c = Cluster(goal_id=g.id, center_node_id=n1.id, gravity_mean=0.8, stability_score=0.9, cycle=1)
    store.insert_cluster(c, {n1.id: 0.9, n2.id: 0.7})

    clusters = store.clusters_for_goal(g.id)
    assert len(clusters) == 1
    assert clusters[0].gravity_mean == 0.8

    members = store.cluster_members(c.id)
    assert len(members) == 2
    assert members[0][1] >= members[1][1]  # DESC


def test_emergence_event(store):
    g = Goal(text="시험", kind="research")
    store.insert_goal(g)
    trigger = Node(kind="entity", text="중심")
    store.insert_node(trigger)

    ev = EmergenceEvent(
        goal_id=g.id,
        trigger_node_id=trigger.id,
        connected_node_ids=["a", "b", "c", "d"],
        new_node_candidate="새 상위 지식",
    )
    store.insert_emergence(ev)

    events = store.emergence_for_goal(g.id)
    assert len(events) == 1
    assert events[0].connected_node_ids == ["a", "b", "c", "d"]
    assert events[0].new_node_candidate == "새 상위 지식"


def test_selection_log_repeat_rate(store):
    g = Goal(text="시험", kind="proposal")
    store.insert_goal(g)
    n = Node(kind="fact", text="X")
    store.insert_node(n)

    for cycle, kept in enumerate([True, True, False, True, True]):
        store.log_selection(g.id, cycle, [(n.id, 0.8, kept)])

    rate = store.repeat_selection_rate(g.id, n.id, last_n=5)
    assert abs(rate - 0.8) < 1e-6
