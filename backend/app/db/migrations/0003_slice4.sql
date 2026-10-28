-- Slice 4 (ADR-017, ADR-010, ADR-011).
-- project_stages.status is the pipeline column. progress is PENDING, CURRENT,
-- DONE, or a terminal code. projects.current_status stays a cache.
-- External MCP URLs live here. Their bearer tokens stay in system_credentials.

ALTER TABLE project_stages ADD COLUMN progress TEXT NOT NULL DEFAULT 'PENDING';

ALTER TABLE project_chats ADD COLUMN tool_name TEXT;
ALTER TABLE project_chats ADD COLUMN tool_arguments TEXT;

CREATE TABLE external_mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE external_mcp_tools (
    server_id TEXT NOT NULL REFERENCES external_mcp_servers(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    input_schema TEXT NOT NULL DEFAULT '{}',
    whitelisted INTEGER NOT NULL DEFAULT 0 CHECK (whitelisted IN (0, 1)),
    PRIMARY KEY (server_id, tool_name)
);
