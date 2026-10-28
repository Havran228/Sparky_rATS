# Sparky_rATS Architecture Decision Records

This file is the decision log for Sparky_rATS. Each record follows the Nygard format: Context,
Decision, Alternatives considered, Consequences. Records are append-only; a decision that is
later changed gets a new record that supersedes the old one rather than an edit.

Baseline: the *Sparky_rATS Master Architecture and Design Specification* (v0.9 → v1.2) as reviewed
on 2026-10-07. Where a record changes something that specification said, the "Supersedes" line
names it.

Status values: `Proposed` (awaiting owner sign-off), `Accepted`, `Superseded by ADR-NNN`,
`Deprecated`.

## Index

| ID | Title | Status |
| --- | --- | --- |
| [ADR-001](#adr-001) | Deployment topology: Docker Compose with three services | Proposed |
| [ADR-002](#adr-002) | Inference runtime and model selection: Ollama with role-based, configurable models | Proposed |
| [ADR-003](#adr-003) | Relational storage: SQLite in WAL mode with FTS5 and SQL migrations | Proposed |
| [ADR-004](#adr-004) | Vector storage: embedded Qdrant confined to a single backend process | Proposed |
| [ADR-005](#adr-005) | Inbox watcher runs inside the backend process | Proposed |
| [ADR-006](#adr-006) | Ingestion pipeline: JSON-LD first, headless browser as lazy fallback, Docling plus vision for files | Proposed |
| [ADR-007](#adr-007) | Slovak payroll engine: year-parameterized rules, Decimal arithmetic, TPP only in v1 | Proposed |
| [ADR-008](#adr-008) | Matching engine: deterministic scoring, LLM classification with verified citations, word-boundary lexical matching | Proposed |
| [ADR-009](#adr-009) | Document generation: structured JSON model rendered by fixed Typst templates, sandboxed compile, round-trip ATS check | Proposed |
| [ADR-010](#adr-010) | Tools and MCP: single registry, Ollama native tool-calling internally, FastMCP streamable HTTP externally, optional MCP client | Proposed |
| [ADR-011](#adr-011) | Google Workspace integration: read-only scopes, transport interceptor with OAuth allowlist, Workspace-Internal app, clipboard staging | Proposed |
| [ADR-012](#adr-012) | Secrets management: Fernet-encrypted blobs in SQLite with a file-held master key | Proposed |
| [ADR-013](#adr-013) | Network exposure and authentication: Tailscale-only binding with identity headers | Proposed |
| [ADR-014](#adr-014) | Long-running work: jobs table with server-sent events | Proposed |
| [ADR-015](#adr-015) | Generated documents are first-class versioned artifacts that feed back into bundles | Proposed |
| [ADR-016](#adr-016) | Candidate profile as a structured singleton | Proposed |
| [ADR-017](#adr-017) | Project status is derived from pipeline stages | Proposed |
| [ADR-018](#adr-018) | LLM output discipline: structured outputs validated by Pydantic everywhere | Proposed |
| [ADR-019](#adr-019) | Delivery strategy: vertical slices, usable after the first | Proposed |
| [ADR-020](#adr-020) | Testing and evaluation: golden tests, fixtures, and a prompt evaluation harness | Proposed |
| [ADR-021](#adr-021) | Missing JSON-LD is parsed from HTML; Crawl4AI stays optional | Proposed |
| [ADR-022](#adr-022) | PDF and DOCX parsing is an optional Docling extra | Proposed |
| [ADR-023](#adr-023) | Generation is grounded on the confirmed profile and compiles from a file inside the sandbox | Proposed |
| [ADR-024](#adr-024) | The backend image installs the Docling parse extra | Proposed |
| [ADR-025](#adr-025) | Kanban writes stages, and the Google callback stays on the UI | Proposed |

---

## ADR-001

### Deployment topology: Docker Compose with three services

**Status:** Proposed
**Supersedes:** Spec §4 and §12 (single `sparky_rats:latest` image, Supervisord as PID 1 managing
Ollama, FastAPI, Next.js, and the watcher).

#### Context

The specification packaged everything into one container: a CUDA base image, Ollama, Python with
Torch and Docling, Playwright with Chromium, and Node.js. That image would exceed 15 GB, every
frontend change would force a full rebuild, a crash loop in one program would be hidden inside a
supervisor, and GPU passthrough would have to be granted to the whole application rather than to
the inference process alone.

#### Decision

Use `docker-compose.yml` with three services:

- `ollama`: the official ARM64 CUDA image (or the host's Ollama daemon, selectable by env), with
  the GPU reservation and `/data/ollama` as `OLLAMA_MODELS`.
- `backend`: FastAPI on Python 3.12 with the Typst binary, Docling, and Playwright installed.
  Hosts the inbox watcher (ADR-005) and the MCP server (ADR-010) as in-process components.
- `frontend`: Next.js standalone server.

No Supervisord. Compose `restart: unless-stopped` provides restart semantics; `docker logs`
provides log access. Shared state is the `/mnt/data/sparky_rats` bind mount.

#### Alternatives considered

- Single container with Supervisord (the spec). Rejected for image size, rebuild cost, and
  the Qdrant lock problem it causes (ADR-004).
- Running Ollama only on the host, outside Docker. Allowed via configuration; the compose service
  is the default because it keeps model storage under `/data`.

#### Consequences

- Three Dockerfiles / images instead of one; faster iteration on each.
- Service-to-service traffic uses compose DNS names (`http://ollama:11434`).
- The backend image is still the heaviest (Torch for Docling); Chromium is installed lazily on
  first fallback use to keep the default image smaller.

---

## ADR-002

### Inference runtime and model selection: Ollama with role-based, configurable models

**Status:** Proposed
**Supersedes:** Spec §3 memory budget and model list (Qwen 2.5 7B / 72B Q4, Qwen2-VL-7B, bge-m3
dense+sparse; `OLLAMA_NUM_PARALLEL=4`; 32k–64k contexts).

#### Context

Verified facts about the target hardware and runtime:

- The GB10 has ~273 GB/s memory bandwidth. Dense 70B-class models at 4-bit decode at roughly
  4.5–6 tok/s with ~80 s time-to-first-token at 32k context. Mixture-of-experts models with 3–12B
  active parameters decode at 40–100 tok/s on the same box.
- Qwen2.5-72B Q4_K_M is ~47 GB, not 44 GB. Its fp16 KV cache is ~0.33 MB/token; at 64k context
  with `OLLAMA_NUM_PARALLEL=4` that is ~84 GB, which breaks the 128 GB budget.
- Qwen2-VL is not in the Ollama library; Qwen2.5-VL and Qwen3-VL are.
- Ollama's embedding endpoint returns dense vectors only; bge-m3 sparse output requires an
  in-process FlagEmbedding runtime.
- The Qwen 2.5 family is two generations old at the time of this decision.

#### Decision

- Ollama remains the inference runtime (ARM64, CUDA 13).
- The backend addresses models by **role**, never by name: `fast` (extraction, tagging,
  OCR cleanup), `heavy` (gap analysis, tailoring, SK/EN translation), `vision` (image-only
  inputs), `embed` (bge-m3, dense 1024-d). Role-to-model mapping lives in `config.py` / env.
- Default candidates, to be confirmed by `scripts/benchmark_models.sh` on the actual machine
  (Slice 5): `fast` = a Qwen3-class MoE (30/35B-A3B), `heavy` = gpt-oss-120b (MXFP4) or a
  Qwen3.x-27B dense, `vision` = Qwen3-VL 8B or Qwen2.5-VL 7B, `embed` = bge-m3.
- Ollama environment: `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_MAX_LOADED_MODELS=3`, `OLLAMA_KEEP_ALIVE` tuned per role.
- Default context for `heavy` is 32k; evaluations that need more are chunked and merged rather
  than pushed to 64k.
- Lexical coverage comes from SQLite FTS5 (ADR-003), not from sparse embeddings.

#### Alternatives considered

- Keep Qwen2.5-72B dense as specified. Rejected: multi-minute generations make tailoring loops
  impractical and the memory budget does not close.
- vLLM or TensorRT-LLM with NVFP4 weights. Faster (up to ~1.6x over Ollama on MoE), but more
  operational surface and ARM wheels lag. Revisit if Ollama throughput proves insufficient.
- Run FlagEmbedding in-process for sparse vectors. Rejected; FTS5 gives exact-term matching,
  which is what the ATS emulation layer needs anyway.

#### Consequences

- Swapping a model is a config change plus re-running the benchmark; no code depends on a model
  name.
- Memory budget becomes: heavy ≤ 65 GB, fast ≤ 20 GB, vision ≤ 7 GB, embed ≤ 1.5 GB, KV ≤ 10 GB
  (q8_0, 32k, parallel 1), OS/DB/runtimes ~10 GB, reserve ≥ 15 GB. Not all four are expected to
  be resident at once; Ollama evicts by `MAX_LOADED_MODELS`.

---

## ADR-003

### Relational storage: SQLite in WAL mode with FTS5 and SQL migrations

**Status:** Proposed
**Refines:** Spec §5.1.

#### Context

Single user, single machine, tens to hundreds of projects, low thousands of document chunks.
The specification chose SQLite in WAL mode with foreign keys but did not define how schema
changes are applied or where exact-term search lives.

#### Decision

- SQLite 3, `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout` set; one connection pool owned
  by the backend process.
- Schema is created and evolved by numbered SQL files in `backend/app/db/migrations/`, applied at
  startup by `database.py` and tracked in a `schema_version` table.
- `document_chunks` is mirrored into an FTS5 virtual table (`unicode61` tokenizer with
  `remove_diacritics 2`) for the lexical matcher.
- All timestamps are ISO-8601 UTC text. All JSON columns are validated by Pydantic models before
  write.

#### Alternatives considered

- PostgreSQL. Rejected for one user on one box; adds a service with no benefit.
- Alembic / SQLAlchemy migrations. Acceptable, but plain SQL files are sufficient and keep the
  dependency footprint small.

#### Consequences

- Backups are a single `sqlite3 .backup` call (ADR-001 compose cron / `scripts/backup.sh`).
- FTS5 handles Slovak diacritics but not inflection; the alias dictionary in ADR-008 covers the
  common cases.

---

## ADR-004

### Vector storage: embedded Qdrant confined to a single backend process

**Status:** Proposed
**Refines:** Spec §5.2 (embedded Qdrant). **Depends on:** ADR-005.

#### Context

`QdrantClient(path=...)` ("local mode") takes an exclusive file lock on its storage directory.
The specification ran the inbox watcher as a separate Supervisord program that would also open
the store, which fails with "Storage folder is already accessed by another instance of Qdrant
client". Local mode is also documented by Qdrant as intended for prototyping, not concurrent
access.

#### Decision

- Keep embedded Qdrant at `/data/qdrant` as specified, with exactly one client instance owned by
  the backend process and created in the FastAPI lifespan.
- Collection `sparky_rats_vectors`, 1024-d, cosine. Payload: `bundle_id`, `project_id`,
  `document_id`, `chunk_index`, `doc_type`, `language`, `section_header`, `content_hash`,
  `token_count`. Keyword indices on `bundle_id`, `project_id`, `doc_type`, `language`.
- The vector store is accessed only through `vector_store.py`; no other module imports
  `qdrant_client`.
- `sqlite-vec` inside the main database is the documented fallback if local-mode Qdrant proves
  limiting. The `vector_store.py` interface is kept narrow (upsert, delete-by-document, search
  with filter) so the swap is local.

#### Alternatives considered

- Qdrant server container. Correct fix for multi-process access, but adds a service for a
  dataset of a few thousand points.
- `sqlite-vec` from the start. Attractive (one file, one process, one backup), but Qdrant's
  filtered search is already specified and well understood; kept as fallback.

#### Consequences

- Any component that writes vectors must live in the backend process (hence ADR-005) or call the
  backend API.
- A stale `.lock` after a crash is handled at startup by checking the owning PID.

---

## ADR-005

### Inbox watcher runs inside the backend process

**Status:** Proposed
**Supersedes:** Spec §12 program `inbox_watcher` as a separate Supervisord process.

#### Context

See ADR-004. A separate watcher process cannot open the embedded vector store, and it would also
need its own LLM client, parser runtime, and database connection.

#### Decision

`services/watcher.py` is a `watchdog` observer started in the FastAPI lifespan. On a new file in
`/data/inbox/`, it debounces, computes a content hash, moves the file to `/data/bundles/_inbox/`,
and enqueues an `ingest_file` job (ADR-014). All parsing and embedding happens in the job runner.

#### Alternatives considered

- Separate process that only `POST`s to the API. Works, but it is a second process to supervise
  for no benefit.

#### Consequences

- Watcher failures surface in the backend log and health endpoint.
- Duplicate drops are detected by hash before any LLM call.

---

## ADR-006

### Ingestion pipeline: JSON-LD first, headless browser as lazy fallback, Docling plus vision for files

**Status:** Proposed
**Refines:** Spec §6.

#### Context

Profesia.sk serves Schema.org `JobPosting` JSON-LD in static HTML for most postings. A headless
Chromium is heavy on ARM64 and should not be on the hot path. Uploaded files are mostly text PDFs;
image-only inputs are the exception.

#### Decision

1. **Classify input**: Profesia ID regex (seven digits, with the `profesia`/`id`/`ponuka`
   prefixes from the spec), Profesia URL regex (`/praca/.../O<7 digits>` or `?id=<7 digits>`),
   otherwise file by MIME type.
2. **Network**: `httpx` async GET with browser-like headers and a per-host rate limit; parse
   `<script type="application/ld+json">` for `JobPosting`; also capture the HTML-to-Markdown body
   as `raw_content_markdown`.
3. **Fallback**: on 403/503, bot challenge, or missing JSON-LD, run Crawl4AI (Chromium installed
   on first use), extract Markdown.
4. **Files**: Docling for PDF/DOCX with heading and reading-order preservation; if text yield is
   below a threshold, send page images to the `vision` role.
5. **Structured extraction**: the `fast` role fills a `JobPostingExtract` Pydantic schema via
   Ollama structured outputs: salary bracket normalization (ranges, "od X", hourly, netto/brutto
   markers), contract type, mandatory requirements, preferred requirements, offerings, language of
   the posting.
6. Raw fetched HTML is cached under `/data/bundles/_raw/` for reproducibility and test fixtures.

#### Alternatives considered

- Always use Crawl4AI. Rejected: slow, heavy, unnecessary for the common path.
- Regex-only extraction. Rejected: postings are free text; the LLM step is where structure comes
  from, and it is constrained by schema.

#### Consequences

- Scraper tests run against cached HTML fixtures, never the live site.
- Scraping is for personal use at low volume; the rate limiter and cache keep it polite.

---

## ADR-007

### Slovak payroll engine: year-parameterized rules, Decimal arithmetic, TPP only in v1

**Status:** Proposed
**Supersedes:** Spec §7 (fixed 13.4% employee deductions, flat NČZD 479.48, two brackets with a
3,961.50 threshold, no assessment cap).

#### Context

The specification mixed 2024 and 2025 parameters and omitted rules that matter at IT salary
levels. Verified 2026 law (Finančná správa information 31/DZPaU/2025/I, Sociálna poisťovňa
tables from 2026-01-01, VšZP 2026 tables):

- Employee: social 9.4% (sickness 1.4, old-age 4.0, disability 3.0, unemployment 1.0) on an
  assessment base capped at 16,764 EUR/month; health **5%** (temporary 2026–2027 increase),
  uncapped. Total 14.4%.
- NČZD: 497.23 EUR/month (5,966.73/year). Full only while annual tax base ≤ 26,083.13; above
  that NČZD = 14,661.11 − base/3, floored at zero (zero from 43,983.32).
- Tax on monthly taxable base: 19% up to 3,665.28; 25% to 5,029.10; 30% to 6,250.86; 35% above.
- Employer: social 25.2%, health 11%, total 36.2%.

Parameters change every January and have changed mid-year before.

#### Decision

- `services/salary_calc.py` computes from a `TaxParams` record loaded from the `tax_params`
  table keyed by tax year (seeded with 2025 and 2026). No numeric constant appears in code.
- `Decimal` arithmetic with per-component rounding to cents, matching payroll practice.
- Output: gross, each employee levy, employee total, tax base, NČZD applied (with phase-out), tax
  per bracket, net, each employer levy, total cost of work.
- v1 models **TPP** (employment contract) only. `SZČO` and `Dohoda` return `not_modeled` with a
  one-line explanation; the UI shows the published figure unconverted.
- Golden tests: official worked examples (e.g., monthly base 6,000 without NČZD → tax 1,328.63)
  and a hand-verified set of IT-range salaries (2,000–7,000 gross) cross-checked against a
  published calculator at implementation time.

#### Alternatives considered

- Hard-code 2026 values. Rejected; the next January breaks it silently.
- Model SZČO in v1. Rejected: levies depend on prior-year income, minimum bases, and expense
  regimes that require user input not present in a posting.

#### Consequences

- Adding 2027 is a data insert plus a test, not a code change.
- The UI calculator exposes the "claim NČZD" toggle and the tax year.

---

## ADR-008

### Matching engine: deterministic scoring, LLM classification with verified citations, word-boundary lexical matching

**Status:** Proposed
**Refines:** Spec §8.

#### Context

The spec's lexical matcher used "exact token and substring frequency counts", which produces
false positives (Java in JavaScript; C, Go, R in almost anything) and misses Slovak inflection and
cross-language postings. It also asked the LLM for a 0–100 score, which is non-deterministic and
not auditable. The product promise is "no invented qualifications", which requires every positive
claim to be traceable.

#### Decision

**Lexical layer (what a corporate ATS sees):**

- Requirement keywords are extracted by the `fast` role into a schema with `term`, `category`
  (technology, framework, certification, degree, language, soft), and `mandatory` flag.
- Terms are expanded through a maintained EN/SK alias dictionary (`K8s`/`Kubernetes`,
  `AWS`/`Amazon Web Services`, `Angličtina`/`English`, declension variants such as
  `Kubernetesom`).
- Matching is word-boundary token matching over FTS5 chunks. No substring matching.
- Output is a `lexical_report`: per term, hit count, documents hit, and whether a mandatory term
  is missing (a "filter hazard").

**Semantic layer:**

- Hybrid retrieval: dense (Qdrant, filtered by selected bundle ids) plus FTS5, reciprocal-rank
  fused, top-k per requirement.
- The `heavy` role classifies each requirement as GREEN / YELLOW / RED with a short rationale and
  a list of cited `chunk_id`s, via structured output.
- The backend verifies that every cited `chunk_id` was in the retrieved set. GREEN without a
  valid citation is downgraded to YELLOW and flagged.
- `match_percentage` is computed in code from explicit, stored weights (default mandatory 0.6,
  preferred 0.3, offerings 0.1; GREEN = 1, YELLOW = 0.5, RED = 0). The LLM never emits a number.
- `match_evaluations` stores `model_name`, `prompt_version`, `score_weights`, and
  `lexical_report`.

**Chunking:** heading-aware chunks of ~400 tokens from Docling structure, with `section_header`
and `content_hash` in payload.

#### Alternatives considered

- Pure LLM scoring. Rejected: not reproducible, not auditable.
- Cross-encoder reranker. Possible later; not needed at this corpus size.

#### Consequences

- Evaluations are comparable across runs and models.
- The alias dictionary is a maintained artifact with its own tests.

---

## ADR-009

### Document generation: structured JSON model rendered by fixed Typst templates, sandboxed compile, round-trip ATS check

**Status:** Proposed
**Supersedes:** Spec §9 and §10 `compile_tailored_pdf(typst_markup)` (LLM emits raw Typst) and
the Chromium HTML-to-PDF fallback.

#### Context

Letting the model author raw Typst produces compile errors, template drift, and a file-read
vector: Typst's `read()`, `image()`, and `json()` can include any file under the compile root in
the PDF. Typst is the right compiler (fast, deterministic, single-column output), but the model
should not control layout.

#### Decision

- The `heavy` role produces a `ResumeModel` or `CoverLetterModel` (Pydantic) via structured
  output: contact, summary, experience entries with bullets, skills groups, education, languages.
- Fixed templates `resume_en.typ`, `resume_sk.typ`, `cover_letter.typ` read the model through
  `sys.inputs` (`typst compile --input data=<json>`). Single column, standard margins, embedded
  Liberation Sans / Roboto via `--font-path`. No tables, text frames, or graphics.
- Compile runs in a throwaway directory with `--root <tmpdir>` so no `/data` file is reachable.
- After compile, `pdftotext` extracts the text layer and the lexical matcher (ADR-008) is re-run
  on it. The result is stored as `ats_roundtrip_report` on the `generated_documents` row (ADR-015).
- Slovak output follows the spec's terminology rules (technical terms stay English, legal
  employment terms in Slovak, formal `vykanie` in cover letters); these are prompt assets under
  `llm/prompts/` with their own version string.
- Raw Typst markup remains available as an advanced tool parameter for manual use, compiled under
  the same sandbox.
- No Chromium HTML-to-PDF fallback.

#### Alternatives considered

- LLM writes Typst (the spec). Rejected for fragility and the file-read risk.
- DOCX output via python-docx from the same model. Not in v1; noted because some recruiters ask
  for DOCX. The JSON model makes it a pure addition.

#### Consequences

- Layout quality is fixed by the template author, not by the model.
- Every generated PDF ships with evidence of what an ATS parser will extract from it.

---

## ADR-010

### Tools and MCP: single registry, Ollama native tool-calling internally, FastMCP streamable HTTP externally, optional MCP client

**Status:** Proposed
**Supersedes:** Spec §10 ("FastMCP exposing tools via standard input/output and local transport to
Ollama").

#### Context

Ollama does not consume MCP servers. It exposes tool-calling: the application sends tool schemas
with a chat request, Ollama returns tool calls, the application executes them. MCP is the protocol
for external clients (n8n, Cursor, Claude Desktop) to call into an application. The spec's wiring
would have produced an MCP server that nothing used. The owner's requirement is that MCP is a real
feature, in particular for external automation such as n8n.

#### Decision

- Tools are defined once as typed Python functions in `backend/app/tools/` with Pydantic
  parameter and result models and a `side_effects` flag. Initial set: `ingest_job`,
  `list_projects`, `get_project_details`, `query_knowledge_bundles`, `evaluate_match`,
  `calc_net_salary`, `compile_tailored_pdf`, `fetch_google_timeline`.
- **Internal**: the project-chat agent loop converts the registry to Ollama tool schemas and
  executes returned calls. Results are stored in `project_chats` with `referenced_chunks`.
- **External (MCP server)**: `mcp_server.py` wraps the same registry with FastMCP and serves it
  over **streamable HTTP** on its own port, bearer-token authenticated, bound to the Tailscale
  interface (ADR-013). Primary consumers: n8n's MCP Client Tool node, Cursor, Claude Desktop.
- **External (MCP client, optional)**: `mcp_client.py` lets the owner register external MCP
  servers (for example an n8n MCP Server Trigger). Discovered tools are disabled by default and
  must be whitelisted per tool in settings before the internal agent loop may call them, because
  external tools can have side effects that Sparky's own tools never have.
- No stdio transport in the deployed service.

#### Alternatives considered

- Drop MCP. Rejected; the owner needs it for external tooling.
- MCP as the only tool interface, with a bridge to Ollama. Rejected; the bridge would just be the
  same registry conversion, and adds a network hop inside the process.

#### Consequences

- One definition per tool; the two surfaces cannot drift.
- n8n can trigger ingestion from chat messages, run nightly evaluations, or pull the Google
  timeline for stale projects without any code in Sparky.
- The "no outbound communication" guarantee (ADR-011) applies to Sparky's own tools; external
  MCP tools are the owner's responsibility, which the whitelist makes explicit.

---

## ADR-011

### Google Workspace integration: read-only scopes, transport interceptor with OAuth allowlist, Workspace-Internal app, clipboard staging

**Status:** Proposed
**Refines and corrects:** Spec §11.

#### Context

The spec's "raise on any non-GET to googleapis.com" would break OAuth itself: token refresh and
revocation are POSTs to `oauth2.googleapis.com`, and Gmail batch endpoints are POST. The spec also
requested `drive.readonly` without a use case, and did not address that Google OAuth apps in
"Testing" status expire refresh tokens after seven days while restricted scopes such as
`gmail.readonly` require verification for public production apps.

#### Decision

- Scopes: `https://www.googleapis.com/auth/gmail.readonly` and
  `https://www.googleapis.com/auth/calendar.events.readonly`. No Drive scope.
- The OAuth client is registered as **Internal** in the owner's Google Workspace organisation,
  which removes the seven-day token expiry and the verification requirement.
- `google_client.py` uses an `httpx` transport hook that: permits `POST` only to
  `oauth2.googleapis.com/token` and `oauth2.googleapis.com/revoke`; permits `GET` to
  `*.googleapis.com`; raises `ReadOnlyViolation` and aborts for anything else, including batch
  endpoints. The hook has unit tests for each branch.
- Gmail and Calendar reads are per-project queries by company domain and contact addresses over a
  configurable lookback window; results are stored as `EMAIL_THREAD` bundle documents only when
  the owner chooses to attach them.
- Drafted replies go to an in-memory **staging buffer** rendered in the UI with a copy button.
  Sparky has no code path that sends email or writes to Calendar.
- "Revoke and wipe" calls the revoke endpoint and deletes the credential row.

#### Alternatives considered

- Google service account with domain-wide delegation. Over-privileged for a personal tool.
- `gmail.metadata` scope. Insufficient; message bodies are needed for timeline context.

#### Consequences

- If the owner does not have a Workspace organisation, the app must be a consumer OAuth app in
  Testing status and the user re-consents weekly; the settings page shows token age.

---

## ADR-012

### Secrets management: Fernet-encrypted blobs in SQLite with a file-held master key

**Status:** Proposed
**Supersedes:** Spec §5.1 `system_credentials` (plaintext `client_secret`, encrypted tokens, no
key management) and the parallel `/data/credentials/` token directory.

#### Context

The spec stored secrets in two places and encrypted only some of them, with no statement of
where the encryption key lives.

#### Decision

- One table `system_credentials(service_name, payload_encrypted, updated_at)`; the payload is a
  Fernet-encrypted JSON blob containing client id, client secret, access and refresh tokens, and
  scopes.
- The master key is read from `SPARKY_MASTER_KEY` if set, otherwise from
  `/data/credentials/master.key` (mode 0600, generated on first start).
- The MCP bearer token and the UI shared secret (ADR-013) are stored the same way.

#### Alternatives considered

- OS keyring. Not available in a container.
- HashiCorp Vault / SOPS. Disproportionate for one user.

#### Consequences

- Backups of the database are useless without the key file; the runbook says to back up both.

---

## ADR-013

### Network exposure and authentication: Tailscale-only binding with identity headers

**Status:** Proposed
**Adds:** the spec had no authentication for the UI or API.

#### Context

The system holds personal documents, recruiter correspondence, and Google tokens, and is reached
from phones and laptops over Tailscale. The spec exposed Next.js on port 3000 with no auth.

#### Decision

- All services bind to the Tailscale interface address (or to localhost behind `tailscale serve`).
  Nothing listens on the LAN interface.
- Identity: `tailscale serve` injects `Tailscale-User-Login`; the backend trusts it only when the
  request arrives from the Tailscale local proxy. A shared bearer secret (ADR-012) is the fallback
  for clients that do not pass through `tailscale serve`, including n8n calling the MCP server.
- The MCP server port and the API port are separate so that external automation can be granted
  MCP access without UI access.

#### Alternatives considered

- Full OIDC login. Unnecessary with a single trusted identity provider already on the network.

#### Consequences

- Anyone with access to the tailnet is one shared secret or one Tailscale identity away from the
  data; tailnet ACLs are part of the runbook.

---

## ADR-014

### Long-running work: jobs table with server-sent events

**Status:** Proposed
**Adds:** the spec had no asynchronous execution model or REST API definition.

#### Context

Ingestion with headless fallback takes seconds; evaluation and generation with the `heavy` role
take tens of seconds to minutes. Blocking HTTP requests would time out and give the UI no progress.

#### Decision

- A `jobs` table: `id`, `type`, `status` (QUEUED, RUNNING, DONE, FAILED, CANCELLED), `progress`
  (0–100 and a message), `payload`, `result`, `error`, timestamps.
- An in-process async worker with a bounded concurrency per job type (LLM jobs serialized, since
  `OLLAMA_NUM_PARALLEL=1`).
- `POST` endpoints that start work return `202` with a job id; `GET /jobs/{id}/events` streams
  SSE; the UI subscribes and updates in place.
- The REST API is specified in OpenAPI by FastAPI and documented per slice.

#### Alternatives considered

- Celery / RQ with Redis. Another service; not needed in one process.
- WebSockets. SSE is sufficient for one-directional progress.

#### Consequences

- Restarting the backend marks RUNNING jobs as FAILED with a restart note; jobs are idempotent by
  design so they can be re-queued.

---

## ADR-015

### Generated documents are first-class versioned artifacts that feed back into bundles

**Status:** Proposed
**Adds:** the spec wrote PDFs to `/data/exports/` with no record.

#### Context

The tailored CV is the product's primary output, and the spec's own bundle concept lists
"previous tailored resumes" as inputs, but nothing tracked which PDF came from which evaluation,
model, or template, nor which one was actually submitted.

#### Decision

- `generated_documents`: `id`, `project_id`, `evaluation_id`, `doc_type`, `language`,
  `template`, `version`, `content_json` (the model from ADR-009), `typst_source`, `pdf_path`,
  `ats_roundtrip_report`, `model_name`, `prompt_version`, `is_submitted`, `created_at`.
- Marking a document as submitted copies it into the project's `active_application` bundle as a
  `CV` or `COVER_LETTER` bundle document, so future evaluations can cite it.
- The UI shows a diff between document versions (content_json level).

#### Consequences

- Exports directory becomes content-addressed storage; the database is the index.

---

## ADR-016

### Candidate profile as a structured singleton

**Status:** Proposed
**Adds:** the spec had no place for the candidate's identity or a skills inventory.

#### Context

Bundles hold documents, but the CV header (name, contact, links), language levels, and a
confirmed skills inventory have to come from somewhere, and the generator must not invent them.

#### Decision

- `candidate_profile`: single row with a Pydantic-validated JSON document: identity, contact,
  links, languages with CEFR levels, a skills inventory (`skill`, `years`, `last_used`,
  `evidence_bundle_ids`), and preferences (NČZD claimed, preferred contract types, locations).
- The inventory can be proposed by the `fast` role from bundles and must be confirmed by the owner
  in the UI before it is used by the generator.

#### Consequences

- Generation and matching have a confirmed, non-hallucinated baseline in addition to raw
  documents.

---

## ADR-017

### Project status is derived from pipeline stages

**Status:** Proposed
**Refines:** Spec §5.1 `projects.current_status` and `project_stages`.

#### Context

The spec kept an enumerated `current_status` on `projects` and an ordered list of stages with
their own statuses, which can disagree.

#### Decision

- `project_stages` is the source of truth. `current_status` on `projects` is a cached value
  recomputed by a service function whenever stages change, and is read-only to the API.
- Status enumeration gains `WITHDRAWN`, `GHOSTED`, and `ON_HOLD`.
- The Kanban UI moves stages, not statuses.

#### Consequences

- No conflicting state; one write path.

---

## ADR-018

### LLM output discipline: structured outputs validated by Pydantic everywhere

**Status:** Proposed

#### Context

Every LLM touchpoint in the design produces data the system acts on: extraction, requirement
keywords, classifications, resume models. Free-text parsing of model output is the main source of
runtime fragility in LLM applications.

#### Decision

- Every LLM call that produces data uses Ollama structured outputs with the JSON schema of a
  Pydantic model as `format`, and the response is validated by that model. Validation failure
  triggers one retry with the error appended, then a job failure.
- Prompts are versioned assets in `llm/prompts/` with a `PROMPT_VERSION` string stored alongside
  results (ADR-008, ADR-015).
- Free-form chat is the only exception, and chat never writes to domain tables directly; it calls
  tools (ADR-010).

#### Consequences

- Model swaps (ADR-002) are safe to test: the schema contract does not change.

---

## ADR-019

### Delivery strategy: vertical slices, usable after the first

**Status:** Proposed
**Supersedes:** Spec §13 task order (storage, scraping, engines, templates, MCP, then UI last).

#### Context

The spec's order delivered no usable product until the final task. The riskiest component (heavy
model throughput and prompt quality) would have been validated late.

#### Decision

Build in slices, each ending in something the owner can use on real postings:

0. Skeleton: compose, scaffolds, schema and migrations, jobs and SSE, Ollama role client, auth.
1. First loop: Profesia ingestion, structured extraction, 2026 salary engine, project list and
   detail UI with gross-to-net panel.
2. Bundles and matching: upload and inbox, parsing, chunking, embeddings, FTS5, evaluation and
   traffic-light UI, evaluation harness.
3. Generation: candidate profile, document models, Typst templates, sandboxed compile, round-trip
   check, versioned documents, preview.
4. Timeline, Google, tools: stages and Kanban, read-only Google client, clipboard staging, tool
   registry, Ollama tool loop, FastMCP server, optional MCP client, project chat.
5. Hardening: backups, model benchmark, runbook.

#### Consequences

- Model defaults (ADR-002) are confirmed with real data by Slice 2, before generation depends on
  them.

---

## ADR-020

### Testing and evaluation: golden tests, fixtures, and a prompt evaluation harness

**Status:** Proposed
**Adds:** the spec had no test or evaluation strategy.

#### Context

Three areas have exact answers (payroll math, regex classification, interceptor behaviour), one
area is live and fragile (scraping), and two are probabilistic (extraction, classification).

#### Decision

- Unit tests with golden values for `salary_calc` (official examples per tax year), input
  classifiers, the alias dictionary, the Google transport hook, and the Typst sandbox.
- Scraper tests against cached HTML fixtures under `backend/tests/fixtures/`; no network in CI.
- An evaluation harness (`backend/evals/`) with a small set of real postings and bundles plus
  expected traffic-light outcomes; reports agreement per model and prompt version so model and
  prompt changes are measured, not guessed.
- Round-trip ATS reports (ADR-009) double as regression tests for the templates.

#### Consequences

- Changing a prompt or a model default requires re-running the harness and recording the result
  in the commit message.

---

## ADR-021

### Missing JSON-LD is parsed from HTML; Crawl4AI stays optional

**Status:** Proposed
**Refines:** ADR-006 item 3 (headless fallback on missing JSON-LD).

#### Context

On the Spark, a normal Profesia response sometimes has no Schema.org `JobPosting` block.
The fetch then required Crawl4AI, which is not installed, and the ingest job failed with
`FallbackUnavailable`. A headless browser is still the wrong tool for a page that already
returned HTML.

#### Decision

- The httpx client sends a current desktop browser `User-Agent` and `Accept-Language: sk,cs,en`.
- When the response is readable and JSON-LD is missing, BeautifulSoup (lxml) reads the title,
  employer, salary text, and description, and turns the description into markdown.
- Crawl4AI runs only after 403, 503, or a bot challenge. If it is not installed, ingestion
  continues with the HTML parse instead of failing.

#### Consequences

- A posting without JSON-LD still becomes a project.
- Chromium is not required for the common path.

---

## ADR-022

### PDF and DOCX parsing is an optional Docling extra

**Status:** Proposed
**Refines:** ADR-006 item 4 (Docling for PDF/DOCX, vision role below a text threshold).

#### Context

Docling pulls in Torch. Installing it in the default image and in CI would make every
backend change rebuild a multi-gigabyte environment, and the common uploads for this
slice are Markdown, plain text, and HTML. Image-only inputs still need the vision role.
PDF and DOCX remain the right Docling job when the owner actually has them.

#### Decision

- Text, Markdown, and HTML are parsed in process, with heading-aware chunks of about 400
  whitespace tokens.
- PDF and DOCX call Docling only when the `parse` extra is installed
  (`pip install 'sparky-rats[parse]'`). If it is missing, the ingest job fails with that
  install hint instead of silently dropping the file.
- Image uploads, and only image uploads on this path, are sent to the vision role as a
  structured transcript. A transcript that fails the schema fails the job.
- Embeddings are best-effort. If the embed role is unreachable, the chunks still land in
  SQLite and FTS5 and the job result says `embedded: false`. Dense retrieval is skipped
  for that document until it is re-ingested.

#### Consequences

- CI and the default image stay free of Torch.
- A CV that is only a PDF does not become searchable until the parse extra is installed.
- Lexical matching still runs when Ollama is down, as long as the text was extracted.

---

## ADR-023

### Generation is grounded on the confirmed profile and compiles from a file inside the sandbox

**Status:** Proposed
**Refines:** ADR-009 (the compile command and who may appear on the page) and ADR-015 (what
"mark as submitted" copies).

#### Context

ADR-009 passes the resume JSON as `typst compile --input data=<json>`. A real CV is larger than
a comfortable argv, and quoting JSON on the command line is brittle. The same record says the
generator must not invent qualifications, but a structured-output model will still name employers
and tools that are not in the profile or the bundles unless something after the model checks.

`pdftotext` is required for the round-trip. A PDF with no text layer cannot be cited later, so
submission has to refuse it. The letter date should be the candidate's local date; this deployment
is in Europe/Bratislava.

#### Decision

- The model JSON is written as `model.json` inside the throwaway `--root`. The template reads
  it with `json(sys.inputs.data)`. `--input data=model.json` passes that relative path.
  `--font-path` points at Liberation Sans and Roboto when those directories exist, and system
  fonts are ignored so the compile does not depend on whatever else is installed.
- The Typst process gets a fresh environment (`PATH`, `LANG`, `HOME` and `TMPDIR` inside the
  root). It does not inherit `SPARKY_MASTER_KEY` or the rest of the server environment. Package
  lookup is pinned to empty directories inside the root. Markup that `#import`s or `#include`s
  is rejected before compile. `#read` of a path outside the root fails at compile time.
- Before compile, the confirmed profile replaces the contact block and the language list.
  Skill items that are not the confirmed inventory, or an alias of it, are dropped. Experience
  and education entries whose company or institution does not occur, on a word boundary, in the
  supplied bundle evidence are dropped. Generation refuses to call the model until the profile
  has a name, at least one confirmed skill, and some evidence text.
- The round-trip report is the lexical matcher run on whitespace-normalized `pdftotext` output.
  When the job was given a match evaluation, the terms are that evaluation's keyword list;
  otherwise they are the posting's requirement lines. `mandatory_missing` is stored on the row.
- Marking a document submitted copies the PDF into the project's `active_application` bundle
  (created if needed), chunks the PDF text, and embeds when the embed role answers. A second
  submit of the same bytes does not insert another document. An empty text layer fails the job.
- Cover-letter dates use `SPARKY_LETTER_TIMEZONE`, default `Europe/Bratislava`.
- Raw markup is still compiled, under the same root, and stored with `template = raw`. It does
  not go through the model.

#### Consequences

- The diff of two versions is a diff of the grounded JSON, which is what was compiled.
- A skill the model invented shows up in `ats_roundtrip_report.grounding.dropped_skills` and
  not in the PDF.
- CI installs the pinned Typst binary, Poppler, Liberation Sans, and Roboto. The backend image
  does the same.

---

## ADR-024

### The backend image installs the Docling parse extra

**Status:** Proposed
**Refines:** ADR-022 consequence that the default image stays free of Torch.

#### Context

ADR-022 kept Docling out of the image so a backend rebuild would not pull Torch. PDF and DOCX
uploads then fail in the container until someone installs the extra by hand. The owner wants
that parser present on every start, including on the Spark where a later pip install is awkward.

#### Decision

- `backend/Dockerfile` installs `.[dev,parse]`, so Docling is part of the image.
- The image apt line includes `libgl1`, `libglib2.0-0`, and `poppler-utils`. The first two are
  what Docling's OpenCV import needs. Poppler supplies `pdftotext`.
- CI still installs the backend without the parse extra. Unit tests do not convert real PDFs.

#### Consequences

- A backend image rebuild downloads Torch and is much larger.
- `from docling.document_converter import DocumentConverter` works in the container.
- A checkout outside Docker still needs `pip install 'sparky-rats[parse]'` for PDF and DOCX.

---

## ADR-025

### Kanban writes stages, and the Google callback stays on the UI

**Status:** Proposed
**Refines:** ADR-011 (where the OAuth redirect lands) and ADR-017 (how a stage becomes the
cached project status).

#### Context

`project_stages` already had a `status` column, and `projects.current_status` was documented as
a cache, but nothing said which write the Kanban performs. A Google redirect cannot hit a bearer-
protected API: the browser arrives without the Sparky token. External MCP servers also need a
place to store a URL that is not secret and a bearer that is.

#### Decision

- `project_stages.status` is the pipeline code (`NEW`, `CV_DRAFT`, `SUBMITTED`, `SCREENING`,
  `INTERVIEW`, `OFFER`, or a custom uppercase code). `progress` is `PENDING`, `CURRENT`, `DONE`,
  or a terminal code (`ON_HOLD`, `REJECTED`, `WITHDRAWN`, `GHOSTED`).
- `derive_status` reads those rows. A terminal progress outranks an open stage. The service
  writes the result into `projects.current_status`. No API accepts that column as input.
- Moving a Kanban card calls `POST /projects/{id}/move` with a column code. That updates stage
  progress and then the cache.
- The OAuth redirect URI is the Next.js page `/settings/google/callback`. That page posts the
  code and the single-use state to an authenticated exchange route. The state is a Fernet blob.
- Clipboard drafts live in process memory. A restart drops them. They are not credentials and
  they are not sent.
- An attached Gmail message is a bundle document of type `EMAIL_THREAD`.
- External MCP server URLs live in SQLite. Their bearer tokens live in `system_credentials`.
  Discovered tools start with `whitelisted = 0`. The chat loop may call one only after that flag
  is set. Every external tool is treated as having side effects.
- FastMCP serves streamable HTTP at `/mcp` on the MCP port. Responses use JSON bodies. The only
  accepted credential on that port is the MCP bearer.

#### Consequences

- A project created before this slice, with no stage rows, is given the default pipeline the
  first time stages are read, and its cached status becomes `NEW`.
- The Google Cloud redirect URI has to be the UI origin, not the API origin.
- n8n's MCP Client Tool node uses `http://<tailscale-host>:8001/mcp` and `SPARKY_MCP_TOKEN`.
