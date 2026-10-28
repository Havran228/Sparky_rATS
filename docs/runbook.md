# Sparky_rATS runbook

Operations for one machine, the Spark, on the tailnet. Application decisions are in
[ADR.md](ADR.md). Slice 5's backup and benchmark rules are [ADR-026](ADR.md#adr-026).

Times below are Europe/Bratislava unless a stamp says `Z` (UTC). Backup directories use UTC
stamps. The scheduler uses Europe/Bratislava.

## First start

On the Spark:

```bash
sudo mkdir -p /mnt/data/sparky_rats
cp .env.example .env
```

Edit `.env`:

- `SPARKY_BIND_HOST` is the Tailscale IP of the Spark, or `127.0.0.1` when you only use
  `tailscale serve` from the box itself.
- `SPARKY_API_TOKEN` and `SPARKY_MCP_TOKEN` are two different long random strings. Leave them
  empty to let the backend generate both on first start and write them under
  `/mnt/data/sparky_rats/credentials/` mode 0600.
- Leave `SPARKY_MASTER_KEY` empty. The backend writes `credentials/master.key` mode 0600.

```bash
docker compose up --build -d
curl -fsS "http://${SPARKY_BIND_HOST:-127.0.0.1}:${SPARKY_API_PORT:-8002}/health"
```

`GET /health` is public. `status` is `ok` when the database, Qdrant, Ollama, the watcher, and
the MCP server are up. Ollama stays unpublished. The backend reaches it at `http://ollama:11434`.

Copy `master.key` off the machine before you put real documents in. A database backup cannot
be decrypted without it. The backup sidecar does not copy it.

Host ports: UI `3000`, API `8002` (the container listens on 8000; host 8000 is Portainer),
MCP `8001`. Nothing in this compose file listens on the LAN address.

## Tailnet

Bind published ports to the Tailscale interface:

```bash
# .env
SPARKY_BIND_HOST=100.x.y.z
```

Example ACL, for the owner's identity only. Use your tailnet's real names.

```json
{
  "acls": [
    {"action": "accept", "src": ["your-login@example.com"], "dst": ["spark:3000,8001,8002"]}
  ]
}
```

`tailscale serve` can put HTTPS in front of the UI. It injects `Tailscale-User-Login`. The
backend trusts that header only when the TCP peer is in `SPARKY_TAILSCALE_PROXY_HOSTS`. Behind
Docker that peer is the bridge gateway, not the laptop, so set the gateway address there.
Clients that do not come through `tailscale serve` use `Authorization: Bearer` and the API token.
The MCP port does not accept the Tailscale header. It accepts `SPARKY_MCP_TOKEN` only.

## Google OAuth

The client is a Workspace-Internal OAuth client. Scopes are only:

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/calendar.events.readonly`

In Google Cloud, inside the Workspace organisation:

1. Create an OAuth client of type Web application.
2. Set the authorised redirect URI to the UI origin plus `/settings/google/callback`.
   The default is `http://127.0.0.1:3000/settings/google/callback`. On the tailnet it is
   `http://<tailscale-ip>:3000/settings/google/callback`, or the `tailscale serve` HTTPS origin.
   Set the same value in `SPARKY_GOOGLE_REDIRECT_URI`. The Settings page shows the URI the
   backend is using.
3. Open Settings in the UI. Paste the client id and secret. They are stored as a Fernet blob.
4. Choose Connect. Google redirects to the UI callback, and that page posts the code to the API
   with the UI's bearer token. The state value is single-use.
5. Revoke and wipe is on the same page. It calls Google's revoke endpoint and deletes the row.

A consumer OAuth app left in Testing status expires the refresh token after seven days. The
Settings page shows the token age and, once that age is a week, reminds you of that Testing
limit. A Workspace-Internal client does not have the seven-day expiry.

Timeline queries are per project (company domain, contact addresses, lookback, default 90 days).
Attaching a message stores an `EMAIL_THREAD` bundle document. Drafts sit in process memory with
a copy button. A restart drops them. Sparky has no send path.

## n8n and other MCP clients

The MCP server is streamable HTTP with JSON responses:

```text
http://<tailscale-host>:8001/mcp
Authorization: Bearer <SPARKY_MCP_TOKEN>
```

In n8n, the MCP Client Tool node uses that URL and that bearer. Give n8n the MCP token, not the
API token, so automation does not receive the UI credential.

Tools from the same registry the chat uses: `ingest_job`, `list_projects`,
`get_project_details`, `query_knowledge_bundles`, `evaluate_match`, `calc_net_salary`,
`compile_tailored_pdf`, `fetch_google_timeline`.

To let Sparky call an external MCP server (for example an n8n MCP Server Trigger): Settings,
add the server URL and its bearer. Discover tools. Each tool starts disabled. The chat loop
calls one only after you whitelist it. Every external tool is treated as having side effects.
The bearer for that server is in `system_credentials`, not in the server row.

## Backups

The `backup` container runs at 03:15 Europe/Bratislava. If it was down at that minute, the next
start catches up once for that local day. Retention is 14 days (`SPARKY_BACKUP_RETENTION_DAYS`).

Each directory is `/data/backups/YYYYMMDDTHHMMSSZ/` and contains:

- `sparky_rats.db`, from SQLite's online backup API
- `qdrant/points.jsonl` and `qdrant/collection.json` when the backend did the snapshot
- `MANIFEST.txt` with `master_key: NOT INCLUDED`

The live path is `POST /maintenance/backup` with the API bearer. The sidecar calls that route.
If the API cannot be reached, the script falls back to `sqlite3 .backup` and copies the Qdrant
directory only when `.lock` is not held. Exit code 3 means the sqlite file was saved and Qdrant
was left alone because it was locked. An HTTP error does not fall back.

Run one backup now:

```bash
docker compose exec -T backup /usr/local/bin/backup.sh
```

From a checkout, against a stopped backend:

```bash
SPARKY_DATA_DIR=/mnt/data/sparky_rats SPARKY_BACKUP_DIRECT=1 scripts/backup.sh
```

Store `credentials/master.key` yourself, mode 0600, outside `/data/backups/` and preferably off
the Spark. Also keep `api_token` and `mcp_token` if you did not set them in `.env`. Those three
files are not in the archive. The encrypted credential rows are inside the sqlite file and are
useless without `master.key`.

## Restore

Stop the backend so nothing holds the database or the Qdrant lock. The backup container can
stay up.

```bash
docker compose stop backend
SPARKY_DATA_DIR=/mnt/data/sparky_rats scripts/restore.sh /mnt/data/sparky_rats/backups/<stamp>
SPARKY_DATA_DIR=/mnt/data/sparky_rats scripts/restore.sh --yes <stamp>
```

The first command prints the plan and changes nothing.

- `qdrant: directory` in the manifest: `--yes` replaces the Qdrant directory.
- `qdrant: points.jsonl`: `--yes` replaces the sqlite file only. Start the backend, then:

```bash
curl -fsS -X POST "http://${SPARKY_BIND_HOST}:${SPARKY_API_PORT:-8002}/maintenance/restore" \
  -H "Authorization: Bearer ${SPARKY_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name":"<stamp>","replace":true}'
```

`replace: true` deletes the vector collection and upserts the snapshot. Retry that call if it
fails after the delete; the snapshot file is still in the backup directory.

- `qdrant: skipped-locked`: sqlite is restored and the vectors were not. Lexical search still
  works from FTS5. Dense search needs those documents ingested again.

Put `master.key` back in `credentials/` before you start the backend, or Fernet decryption
fails. Then:

```bash
docker compose start backend
curl -fsS "http://${SPARKY_BIND_HOST}:${SPARKY_API_PORT:-8002}/health"
```

## Model benchmark and swap

Ollama is not published. From the repo on the Spark, with the stack up:

```bash
docker compose run --rm --no-deps --entrypoint bash \
  -v "$PWD/scripts:/scripts:ro" \
  -v "$PWD/docs:/docs" \
  -v "$PWD/backend:/backend:ro" \
  -e OLLAMA_HOST=http://ollama:11434 \
  -e PYTHONPATH=/backend \
  -e SPARKY_BENCH_MARKDOWN=/docs/benchmarks.md \
  -e SPARKY_BENCH_SAMPLES=/docs/benchmarks.jsonl \
  backend bash /scripts/benchmark_models.sh
```

If Ollama runs on the host instead of in Compose, `OLLAMA_HOST=http://127.0.0.1:11434` is enough
and you can run `scripts/benchmark_models.sh` on the host. The script pulls
`qwen3:30b-a3b`, `gpt-oss:120b`, `qwen3:32b`, `qwen3-vl:8b`, `qwen2.5vl:7b`, and `bge-m3`
unless you override `SPARKY_BENCH_FAST`, `SPARKY_BENCH_HEAVY`, `SPARKY_BENCH_VISION`, and
`SPARKY_BENCH_EMBED`. A 32k prefill on a large model can take minutes. The prompt is synthetic.
The script does not read `/data`.

`docs/benchmarks.md` then lists TTFT, decode tok/s, and a suggested `.env` block. `fast` is the
best qualifying 8k sample. `heavy` prefers 32k. `vision` is the image prompt. `embed` must be
1024-dimensional. Ties keep the model name that sorts first.

Put the suggested lines in `.env` and recreate the backend:

```bash
docker compose up -d backend
```

Do not commit `.env`. Commit `docs/benchmarks.md` and `docs/benchmarks.jsonl` when you want the
measurement in git. Change the defaults in `backend/app/config.py` only when a checkout with no
`.env` should pick up the new names.

Memory budget from ADR-002, with `OLLAMA_NUM_PARALLEL=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`:
heavy ≤ 65 GB, fast ≤ 20 GB, vision ≤ 7 GB, embed ≤ 1.5 GB, KV ≤ 10 GB at 32k. Ollama keeps at
most three models loaded.

The offline harness, from `backend/` with the package installed:

```bash
python -m evals
```

On the committed cases that prints agreement 1.0 for `offline-lexical` / `classify_match_v1`.
Re-run it after a prompt change. A heavy-model comparison is a later run with a classifier
passed into `evals.runner.run_cases`; record the model name and `prompt_version` in the commit
message (ADR-020).

## Accepting the ADRs

Status stays Proposed until you have a Spark benchmark in `docs/benchmarks.md` and you want to
sign off. Add ADR-027 that lists the measured role defaults, the benchmark date, and the eval
result you are willing to stand behind. That record is the acceptance. Leave the earlier
decision text as it is.
