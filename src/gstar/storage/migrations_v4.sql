-- Schema v4 — Phase B1 (worker + community detection)

-- 커뮤니티 canonical: Louvain 등 detection 알고리즘 산출물 보관
CREATE TABLE IF NOT EXISTS community_canonical (
    id            VARCHAR PRIMARY KEY,
    project_id    VARCHAR NOT NULL,          -- 집계 대상 entity_canonical.project_id
    algorithm     VARCHAR NOT NULL,          -- "louvain" | "label_propagation" | ...
    members_json  VARCHAR NOT NULL DEFAULT '[]',   -- entity_canonical.id[] (해당 커뮤니티 멤버)
    size          INTEGER NOT NULL DEFAULT 0,      -- len(members_json)
    label         VARCHAR,                    -- 대표 entity canonical_name (top-1 mention)
    created_at    TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comm_project ON community_canonical(project_id);
CREATE INDEX IF NOT EXISTS idx_comm_alg     ON community_canonical(algorithm);

-- entity_canonical.community_id — 최신 community 할당
ALTER TABLE entity_canonical ADD COLUMN IF NOT EXISTS community_id VARCHAR;
CREATE INDEX IF NOT EXISTS idx_ent_community ON entity_canonical(community_id);

-- worker 상태 보관 (tick 로그)
CREATE TABLE IF NOT EXISTS worker_tick (
    id              VARCHAR PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    duration_ms     INTEGER,
    status          VARCHAR NOT NULL DEFAULT 'running',   -- running | success | failed | paused
    steps_json      VARCHAR NOT NULL DEFAULT '{}',        -- {community: {...}, reinforce: {...}}
    error           VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_tick_status ON worker_tick(status);
CREATE INDEX IF NOT EXISTS idx_tick_started ON worker_tick(started_at);

INSERT INTO schema_version (version) VALUES (4) ON CONFLICT DO NOTHING;
