-- Schema v6 — Phase G (Claude-bracketed trace + procedure mining)

-- node 에 trace 전용 메타 필드 (Node(kind='trace'|'procedure') 용 확장 속성)
ALTER TABLE node ADD COLUMN IF NOT EXISTS trace_meta_json VARCHAR DEFAULT '{}';

-- Claude Code 세션의 상세 trace. node 테이블과 1:1 매핑 (id = node.id).
CREATE TABLE IF NOT EXISTS claude_trace (
    id              VARCHAR PRIMARY KEY,          -- Node.id 와 동일
    task_id         VARCHAR,                      -- bracket open/close 가 묶는 작업 id
    bracket_phase   VARCHAR NOT NULL,             -- open | step | tool_use | decision | close | session
    source          VARCHAR NOT NULL,             -- mcp | skill | hook
    description     VARCHAR NOT NULL,
    inputs_json     VARCHAR NOT NULL DEFAULT '{}',
    outputs_json    VARCHAR NOT NULL DEFAULT '{}',
    thinking_text   VARCHAR,
    extras_json     VARCHAR NOT NULL DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_task   ON claude_trace(task_id);
CREATE INDEX IF NOT EXISTS idx_trace_phase  ON claude_trace(bracket_phase);
CREATE INDEX IF NOT EXISTS idx_trace_source ON claude_trace(source);

INSERT INTO schema_version (version) VALUES (6) ON CONFLICT DO NOTHING;
