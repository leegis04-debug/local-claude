"""G 핵심 데이터 모델 (pydantic).

Node = 블록체인적 최소 단위. kind 로 사실/개체/관계/이벤트/상태/근거 구분.
Edge = 노드 간 연결. 3개까지 안정, 4번째 연결은 창발 신호.
Goal = 중력장의 중심. Cluster = 응집된 지식 항성.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID


def _new_id() -> str:
    return str(ULID())


def _now() -> datetime:
    return datetime.now(timezone.utc)


NodeKind = Literal[
    "fact", "entity", "relation", "event", "state", "evidence",
    # Phase G6 — worker procedure miner 가 생성하는 절차 패턴 노드
    "procedure",
]
GoalKind = Literal["proposal", "code", "research"]

DEFAULT_NAMESPACE = "personal"


class Node(BaseModel):
    """지식 원자. embedding 은 FAISS 인덱스에서 별도 관리.

    Integrity 필드(content_hash, prev_hash, signer_id, signature)는 insert 시
    DuckStore 가 자동으로 계산·채운다. 빈 값으로 생성해도 DB 에 들어가면 확정됨.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_id)
    kind: NodeKind
    text: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    version: int = 1
    prev_version_id: str | None = None
    # Integrity Layer (Week 3.5)
    source_namespace: str = DEFAULT_NAMESPACE
    content_hash: str = ""            # insert 시 DuckStore 가 채움
    prev_hash: str | None = None      # 같은 namespace 내 직전 노드의 content_hash
    signer_id: str | None = None      # 미래 확장: 공개키 fingerprint
    signature: str | None = None      # 미래 확장: Ed25519 서명


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_id)
    src: str
    dst: str
    kind: str
    weight: float = 1.0
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_id)
    text: str
    kind: GoalKind
    created_at: datetime = Field(default_factory=_now)


class Cluster(BaseModel):
    """지식 항성 후보. node_ids 는 cluster_member 테이블로 정규화."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_id)
    goal_id: str
    center_node_id: str | None = None
    gravity_mean: float = 0.0
    stability_score: float = 0.0
    cycle: int = 0
    created_at: datetime = Field(default_factory=_now)
    merkle_root: str | None = None  # 멤버 content_hash Merkle root (Week 3.5)


class Namespace(BaseModel):
    """이직·공유 대비 source_namespace 레지스트리."""

    model_config = ConfigDict(extra="forbid")

    name: str
    created_at: datetime = Field(default_factory=_now)
    is_active: bool = False
    description: str = ""


class EmergenceEvent(BaseModel):
    """4번째 연결 생성 이벤트. 재분해 전 로그 보존."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_new_id)
    goal_id: str
    trigger_node_id: str
    connected_node_ids: list[str]
    new_node_candidate: str | None = None
    created_at: datetime = Field(default_factory=_now)
