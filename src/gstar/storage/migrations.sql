-- G 지식 항성 모델 DuckDB 스키마 v1.
-- 임베딩 벡터는 FAISS 인덱스 파일에서 별도 관리 (여기엔 id 매핑만).

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS node (
    id                VARCHAR PRIMARY KEY,
    kind              VARCHAR NOT NULL,
    text              VARCHAR NOT NULL,
    attrs_json        VARCHAR NOT NULL DEFAULT '{}',
    created_at        TIMESTAMP NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    prev_version_id   VARCHAR,
    -- Integrity Layer (v2)
    source_namespace  VARCHAR NOT NULL DEFAULT 'personal',
    content_hash      VARCHAR NOT NULL DEFAULT '',
    prev_hash         VARCHAR,
    signer_id         VARCHAR,
    signature         VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_node_kind ON node(kind);
CREATE INDEX IF NOT EXISTS idx_node_created ON node(created_at);
CREATE INDEX IF NOT EXISTS idx_node_ns ON node(source_namespace);
CREATE INDEX IF NOT EXISTS idx_node_ns_created ON node(source_namespace, created_at);

CREATE TABLE IF NOT EXISTS edge (
    id              VARCHAR PRIMARY KEY,
    src             VARCHAR NOT NULL,
    dst             VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL,
    weight          DOUBLE NOT NULL DEFAULT 1.0,
    evidence_json   VARCHAR NOT NULL DEFAULT '[]',
    created_at      TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edge_src ON edge(src);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge(dst);
CREATE INDEX IF NOT EXISTS idx_edge_kind ON edge(kind);

CREATE TABLE IF NOT EXISTS goal (
    id         VARCHAR PRIMARY KEY,
    text       VARCHAR NOT NULL,
    kind       VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster (
    id               VARCHAR PRIMARY KEY,
    goal_id          VARCHAR NOT NULL,
    center_node_id   VARCHAR,
    gravity_mean     DOUBLE NOT NULL DEFAULT 0.0,
    stability_score  DOUBLE NOT NULL DEFAULT 0.0,
    cycle            INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMP NOT NULL,
    merkle_root      VARCHAR  -- v2
);

CREATE INDEX IF NOT EXISTS idx_cluster_goal ON cluster(goal_id);

CREATE TABLE IF NOT EXISTS cluster_member (
    cluster_id  VARCHAR NOT NULL,
    node_id     VARCHAR NOT NULL,
    gravity     DOUBLE NOT NULL,
    PRIMARY KEY (cluster_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_cm_node ON cluster_member(node_id);

CREATE TABLE IF NOT EXISTS emergence_event (
    id                  VARCHAR PRIMARY KEY,
    goal_id             VARCHAR NOT NULL,
    trigger_node_id     VARCHAR NOT NULL,
    connected_json      VARCHAR NOT NULL,
    new_node_candidate  VARCHAR,
    created_at          TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_emerge_goal ON emergence_event(goal_id);

-- 선택 사이클 로그. repeat_selection_rate 계산용.
CREATE TABLE IF NOT EXISTS selection_log (
    goal_id    VARCHAR NOT NULL,
    cycle      INTEGER NOT NULL,
    node_id    VARCHAR NOT NULL,
    gravity    DOUBLE NOT NULL,
    kept       BOOLEAN NOT NULL,
    logged_at  TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sel_goal_node ON selection_log(goal_id, node_id);

-- Integrity Layer (v2)
CREATE TABLE IF NOT EXISTS namespace (
    name         VARCHAR PRIMARY KEY,
    description  VARCHAR NOT NULL DEFAULT '',
    is_active    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP NOT NULL
);

INSERT INTO namespace (name, description, is_active, created_at)
VALUES ('personal', '기본 개인 지식 namespace', TRUE, CURRENT_TIMESTAMP)
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
INSERT INTO schema_version (version) VALUES (2) ON CONFLICT DO NOTHING;

-- ========================================================================
-- Schema v3 — Phase A (엔티티 서브시스템) + Phase B (P축 투영)
-- ========================================================================

-- Phase A: 엔티티 canonical + 별칭
CREATE TABLE IF NOT EXISTS entity_canonical (
    id                VARCHAR PRIMARY KEY,
    project_id        VARCHAR NOT NULL,
    track             VARCHAR NOT NULL,            -- "proposal" | "research" | "coding" | "document"
    canonical_name    VARCHAR NOT NULL,
    kind              VARCHAR NOT NULL,
    scope             VARCHAR,                     -- coding: "file:line" 등, 나머지는 NULL
    mentions          INTEGER NOT NULL DEFAULT 0,
    node_id           VARCHAR,                     -- 대응되는 node(kind='entity').id
    created_at        TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ent_project_track ON entity_canonical(project_id, track);
CREATE INDEX IF NOT EXISTS idx_ent_kind ON entity_canonical(kind);
CREATE INDEX IF NOT EXISTS idx_ent_node ON entity_canonical(node_id);

CREATE TABLE IF NOT EXISTS entity_alias (
    alias_surface     VARCHAR NOT NULL,
    canonical_id      VARCHAR NOT NULL,
    project_id        VARCHAR NOT NULL,
    track             VARCHAR NOT NULL,
    first_seen        TIMESTAMP NOT NULL,
    PRIMARY KEY(project_id, track, alias_surface)
);

CREATE TABLE IF NOT EXISTS relation_type (
    name                 VARCHAR PRIMARY KEY,
    description          VARCHAR NOT NULL DEFAULT '',
    applies_to_tracks    VARCHAR NOT NULL DEFAULT ''   -- CSV: "proposal,research"
);

-- 기존 edge.kind 를 유지하면서 relation_type 컬럼 추가 (typed relations).
ALTER TABLE edge ADD COLUMN IF NOT EXISTS relation_type VARCHAR;
UPDATE edge SET relation_type = kind WHERE relation_type IS NULL;

CREATE INDEX IF NOT EXISTS idx_edge_reltype ON edge(relation_type);

-- Phase B: P축 투영 상태
CREATE TABLE IF NOT EXISTS projection_fact (
    id                VARCHAR PRIMARY KEY,
    project_id        VARCHAR NOT NULL,
    track             VARCHAR NOT NULL,
    kind              VARCHAR NOT NULL,
    text              VARCHAR NOT NULL,
    entity_ids_json   VARCHAR NOT NULL DEFAULT '[]',
    source_stage      VARCHAR NOT NULL,
    source_hash       VARCHAR NOT NULL,
    created_at        TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pfact_project_track ON projection_fact(project_id, track);
CREATE INDEX IF NOT EXISTS idx_pfact_stage ON projection_fact(source_stage);

CREATE TABLE IF NOT EXISTS projection_run (
    id                   VARCHAR PRIMARY KEY,
    project_id           VARCHAR NOT NULL,
    track                VARCHAR NOT NULL,
    stage                VARCHAR NOT NULL,
    output_path          VARCHAR NOT NULL,
    citations_json       VARCHAR NOT NULL DEFAULT '[]',
    fact_ids_json        VARCHAR NOT NULL DEFAULT '[]',
    created_at           TIMESTAMP NOT NULL,
    duration_ms          INTEGER,
    ollama_model         VARCHAR,
    coherence_retries    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_prun_project_track ON projection_run(project_id, track);

CREATE TABLE IF NOT EXISTS projection_summary (
    stage         VARCHAR NOT NULL,
    source_hash   VARCHAR NOT NULL,
    depth         VARCHAR NOT NULL,
    track         VARCHAR NOT NULL,
    json          VARCHAR NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(stage, source_hash, depth, track)
);

-- relation_type seed 데이터 (Phase A 가 사용하는 17종)
INSERT INTO relation_type (name, description, applies_to_tracks) VALUES
    ('co_occurs',        '같은 fact 에 공출현',         'proposal,research,coding,document'),
    ('evidence_of',      '엔티티 → 증거 fact',          'proposal,research,coding,document'),
    ('measures',         '지표 → 프로젝트/기술',        'proposal,research'),
    ('participates_in',  '사람 → 프로젝트/조직',        'proposal,research'),
    ('depends_on',       '기술/모듈 의존',              'proposal,research,coding'),
    ('budgets_for',      '예산 → 프로젝트',             'proposal'),
    ('schedules',        '일정 → 프로젝트',             'proposal,research'),
    ('tests',            '방법 → 가설',                 'research'),
    ('uses',             '실험 → 데이터/방법',          'research,coding'),
    ('yields',           '실험 → 결과',                 'research'),
    ('calls',            '함수 → 함수',                 'coding'),
    ('imports',          '모듈 → 모듈',                 'coding'),
    ('tested_by',        '함수 → 테스트',               'coding'),
    ('defines',          '모듈 → 클래스/함수',          'coding'),
    ('cites',            '주장 → 인용',                 'document,research'),
    ('supports',         '인용 → 주장',                 'document,research')
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version) VALUES (3) ON CONFLICT DO NOTHING;
