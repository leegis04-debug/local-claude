-- Schema v5 — Phase E (Verifier + Reinforce + trust)

-- trust score 및 검증 메타 — Verifier L2 가 업데이트
ALTER TABLE node ADD COLUMN IF NOT EXISTS trust_score      DOUBLE DEFAULT 0.0;
ALTER TABLE node ADD COLUMN IF NOT EXISTS verified_by_json VARCHAR DEFAULT '[]';
ALTER TABLE node ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_node_trust ON node(trust_score);

-- verifier 실행 로그 — 재현성·디버깅
CREATE TABLE IF NOT EXISTS verification_log (
    id             VARCHAR PRIMARY KEY,
    node_id        VARCHAR NOT NULL,
    layer          VARCHAR NOT NULL,          -- "L1" (rules) | "L2" (rag_cross)
    pass           BOOLEAN NOT NULL,
    delta          DOUBLE NOT NULL DEFAULT 0.0,    -- trust_score 변화량
    evidence_json  VARCHAR NOT NULL DEFAULT '{}',
    created_at     TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_verif_node  ON verification_log(node_id);
CREATE INDEX IF NOT EXISTS idx_verif_layer ON verification_log(layer);

INSERT INTO schema_version (version) VALUES (5) ON CONFLICT DO NOTHING;
