"""개별 점수 함수 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from gstar.gravity.score import (
    centrality,
    purpose_fit,
    recency,
    relevance,
    version_validity,
)
from gstar.schema import Goal, Node


def test_relevance_identical_is_one():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert abs(relevance(v, v) - 1.0) < 1e-6


def test_relevance_orthogonal_is_half():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    # cosine = 0 → (0+1)/2 = 0.5
    assert abs(relevance(a, b) - 0.5) < 1e-6


def test_relevance_opposite_is_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([-1.0, 0.0], dtype=np.float32)
    assert abs(relevance(a, b)) < 1e-6


def test_recency_monotonic_decay():
    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    recent = now - timedelta(days=1)
    month_ago = now - timedelta(days=30)
    old = now - timedelta(days=120)

    r_recent = recency(recent, halflife_days=30, now=now)
    r_month = recency(month_ago, halflife_days=30, now=now)
    r_old = recency(old, halflife_days=30, now=now)

    assert r_recent > r_month > r_old
    assert abs(r_month - 0.5) < 0.01  # halflife 지점


def test_centrality_seed_node():
    adj = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}
    assert centrality("a", {"a"}, adj) == 1.0


def test_centrality_hop_distance():
    adj = {"s": {"a"}, "a": {"s", "b"}, "b": {"a", "c"}, "c": {"b"}}
    # s → a (1 hop) → 1/2
    assert abs(centrality("a", {"s"}, adj) - 0.5) < 1e-6
    # s → b (2 hops) → 1/3
    assert abs(centrality("b", {"s"}, adj) - 1.0 / 3) < 1e-6
    # beyond max_hops
    assert centrality("c", {"s"}, adj, max_hops=1) == 0.0


def test_version_validity():
    n1 = Node(kind="fact", text="단일 버전")
    assert version_validity(n1) == 0.8

    n2 = Node(kind="fact", text="최신", prev_version_id="prev")
    assert version_validity(n2, is_latest=True) == 1.0

    assert version_validity(n2, is_latest=False) < 0.5


def test_purpose_fit_proposal_bonus_for_section():
    goal = Goal(text="사업계획서", kind="proposal")
    n = Node(kind="entity", text="KPI", attrs={"section": "목표"})
    base = purpose_fit(Node(kind="entity", text="x"), goal)  # section 없음
    with_bonus = purpose_fit(n, goal)
    assert with_bonus > base


def test_purpose_fit_code_kind():
    goal = Goal(text="API 설계", kind="code")
    s = Node(kind="state", text="현재 상태")
    # code + state 는 1.0 기본
    assert purpose_fit(s, goal) >= 0.9
