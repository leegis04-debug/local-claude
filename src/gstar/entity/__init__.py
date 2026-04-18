"""G 엔티티 서브시스템 (Phase A).

범용 트랙(proposal/research/coding/document) 모두를 위한 엔티티 타입 체계,
한국어 형태소 기반 normalizer, 규칙 classifier, canonical linker, typed edge graph.
"""

from gstar.entity.types import (
    CanonicalEntity,
    Edge,
    Entity,
    EntityKind,
    FactRef,
    RelationType,
    Track,
)

__all__ = [
    "CanonicalEntity",
    "Edge",
    "Entity",
    "EntityKind",
    "FactRef",
    "RelationType",
    "Track",
]
