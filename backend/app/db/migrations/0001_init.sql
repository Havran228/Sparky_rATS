-- Sparky_rATS initial schema (README domain model, ADR-003).
-- JSON columns are validated by Pydantic before the code writes them.
-- current_status is a cache. project_stages is the source of truth (ADR-017).
-- Known status values: NEW, CV_DRAFT, SUBMITTED, SCREENING, INTERVIEW, OFFER,
-- REJECTED, WITHDRAWN, GHOSTED, ON_HOLD.

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_ref TEXT,
    company TEXT,
    title TEXT,
    contract_type TEXT,
    salary_json TEXT,
    language TEXT,
    requirements_json TEXT,
    offerings_json TEXT,
    contacts_json TEXT,
    dates_json TEXT,
    raw_content_markdown TEXT,
    current_status TEXT NOT NULL DEFAULT 'NEW',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_stages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, position)
);

CREATE TABLE knowledge_bundles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('historical_baseline', 'active_application')),
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE bundle_documents (
    id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL REFERENCES knowledge_bundles(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    language TEXT,
    extracted_text TEXT,
    content_hash TEXT,
    file_path TEXT,
    mime_type TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES bundle_documents(id) ON DELETE CASCADE,
    bundle_id TEXT NOT NULL,
    project_id TEXT,
    chunk_index INTEGER NOT NULL,
    section_header TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    language TEXT,
    doc_type TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_project_stages_project ON project_stages(project_id, position);
CREATE INDEX idx_bundles_project ON knowledge_bundles(project_id);
CREATE INDEX idx_documents_bundle ON bundle_documents(bundle_id);
CREATE INDEX idx_documents_hash ON bundle_documents(content_hash);
CREATE INDEX idx_chunks_document ON document_chunks(document_id, chunk_index);
CREATE INDEX idx_chunks_bundle ON document_chunks(bundle_id);

-- FTS5 mirror of document_chunks (external content, implicit rowid).
-- unicode61 remove_diacritics 2 folds Slovak diacritics (ADR-003, ADR-008).
CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
    content,
    section_header,
    content='document_chunks',
    content_rowid='rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER document_chunks_ai AFTER INSERT ON document_chunks BEGIN
    INSERT INTO document_chunks_fts(rowid, content, section_header)
    VALUES (new.rowid, new.content, coalesce(new.section_header, ''));
END;

CREATE TRIGGER document_chunks_ad AFTER DELETE ON document_chunks BEGIN
    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content, section_header)
    VALUES ('delete', old.rowid, old.content, coalesce(old.section_header, ''));
END;

CREATE TRIGGER document_chunks_au AFTER UPDATE ON document_chunks BEGIN
    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content, section_header)
    VALUES ('delete', old.rowid, old.content, coalesce(old.section_header, ''));
    INSERT INTO document_chunks_fts(rowid, content, section_header)
    VALUES (new.rowid, new.content, coalesce(new.section_header, ''));
END;

CREATE TABLE candidate_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE match_evaluations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    lexical_report TEXT NOT NULL,
    classification TEXT NOT NULL,
    match_percentage REAL NOT NULL,
    score_weights TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_evals_project ON match_evaluations(project_id, created_at);

CREATE TABLE generated_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    evaluation_id TEXT REFERENCES match_evaluations(id) ON DELETE SET NULL,
    doc_type TEXT NOT NULL,
    language TEXT NOT NULL,
    template TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    typst_source TEXT,
    pdf_path TEXT,
    ats_roundtrip_report TEXT,
    model_name TEXT,
    prompt_version TEXT,
    is_submitted INTEGER NOT NULL DEFAULT 0 CHECK (is_submitted IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (project_id, doc_type, language, version)
);

CREATE TABLE project_chats (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    referenced_chunks TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_chats_thread ON project_chats(project_id, thread_id, created_at);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    progress_message TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    result TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX idx_jobs_status ON jobs(status, created_at);

CREATE TABLE tax_params (
    tax_year INTEGER PRIMARY KEY,
    params_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE system_credentials (
    service_name TEXT PRIMARY KEY,
    payload_encrypted TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
