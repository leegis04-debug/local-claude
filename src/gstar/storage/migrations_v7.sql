-- Schema v7 — Phase H1 (원본→정규화→파생 3단계 재정렬)

-- document/section 노드 종류와 part_of edge 를 위한 테이블은 기존 node/edge 에 그대로 저장.
-- 추가 메타 컬럼: document 의 원본 경로·해시 추적용.
ALTER TABLE node ADD COLUMN IF NOT EXISTS document_hash VARCHAR;      -- 원본 파일 sha1 (document 노드만 채움)

CREATE INDEX IF NOT EXISTS idx_node_document_hash ON node(document_hash);

-- Qdrant / Neo4j 로 파생 복제될 때 추적할 view 링크 (Phase H5/H6)
CREATE TABLE IF NOT EXISTS derived_view (
    g_node_id      VARCHAR NOT NULL,
    view           VARCHAR NOT NULL,            -- "qdrant" | "neo4j"
    external_id    VARCHAR NOT NULL,            -- qdrant point id / neo4j node id
    collection     VARCHAR,                      -- qdrant collection name
    emitted_at     TIMESTAMP NOT NULL,
    status         VARCHAR NOT NULL DEFAULT 'ok',   -- ok | failed | stale
    error          VARCHAR,
    PRIMARY KEY (g_node_id, view, external_id)
);

CREATE INDEX IF NOT EXISTS idx_derived_view_node ON derived_view(g_node_id);
CREATE INDEX IF NOT EXISTS idx_derived_view_status ON derived_view(view, status);

INSERT INTO schema_version (version) VALUES (7) ON CONFLICT DO NOTHING;
