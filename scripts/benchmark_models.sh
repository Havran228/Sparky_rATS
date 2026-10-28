#!/usr/bin/env bash
# Measure ADR-002 candidate models against a local Ollama and write docs/benchmarks.md.
# The prompt is synthetic. This script does not read /data and does not edit config.py.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OLLAMA_HOST=${OLLAMA_HOST:-http://127.0.0.1:11434}
OLLAMA_HOST=${OLLAMA_HOST%/}
OUT_MD=${SPARKY_BENCH_MARKDOWN:-$ROOT/docs/benchmarks.md}
OUT_JSONL=${SPARKY_BENCH_SAMPLES:-$ROOT/docs/benchmarks.jsonl}
FAST=${SPARKY_BENCH_FAST:-qwen3:30b-a3b}
HEAVY=${SPARKY_BENCH_HEAVY:-gpt-oss:120b qwen3:32b}
VISION=${SPARKY_BENCH_VISION:-qwen3-vl:8b qwen2.5vl:7b}
EMBED=${SPARKY_BENCH_EMBED:-bge-m3}
CONTEXTS=${SPARKY_BENCH_CONTEXTS:-8192 32768}
PREDICT=${SPARKY_BENCH_PREDICT:-32}
MAX_TIME=${SPARKY_BENCH_MAX_TIME:-900}
PULL_MAX_TIME=${SPARKY_BENCH_PULL_MAX_TIME:-21600}
SKIP_PULL=${SPARKY_BENCH_SKIP_PULL:-0}

usage() {
  cat <<EOF
Usage: benchmark_models.sh [--render SAMPLES OUTPUT]
       benchmark_models.sh --help

Talks to OLLAMA_HOST (default $OLLAMA_HOST). Writes $OUT_MD
only after /api/tags answers. Suggested role lines go in that file, not config.py.

Candidates (space-separated):
  SPARKY_BENCH_FAST, SPARKY_BENCH_HEAVY, SPARKY_BENCH_VISION, SPARKY_BENCH_EMBED
Contexts for fast and heavy: SPARKY_BENCH_CONTEXTS (default 8192 and 32768).
Vision is one short image prompt. Embed is a three-string batch, not a 32k prefill.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"

if [ "${1:-}" = "--render" ]; then
  exec python3 -m app.services.benchmark_report \
    --samples "${2:?samples jsonl}" \
    --output "${3:?markdown path}" \
    --ran-at "${SPARKY_BENCH_RAN_AT:-}" \
    --host "${SPARKY_BENCH_HOST:-}" \
    --ollama-host "${OLLAMA_HOST}"
fi

command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required" >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "curl is required" >&2
  exit 1
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
samples="$tmp/samples.jsonl"
touch "$samples"

append_sample() {
  python3 - "$@" >>"$samples" <<'PY'
import json
import sys

role, model, kind, ctx, pec, pens, ec, ens, dim, err = sys.argv[1:11]

def num(text: str) -> int:
    return 0 if text in {"", "null"} else int(text)

def opt_int(text: str) -> int | None:
    return None if text in {"", "null"} else int(text)

def opt_str(text: str) -> str | None:
    return None if text in {"", "null"} else text

json.dump(
    {
        "role": role,
        "model": model,
        "kind": kind,
        "requested_ctx": num(ctx),
        "prompt_eval_count": num(pec),
        "prompt_eval_ns": num(pens),
        "eval_count": num(ec),
        "eval_ns": num(ens),
        "dimension": opt_int(dim),
        "error": opt_str(err),
    },
    sys.stdout,
)
sys.stdout.write("\n")
PY
}

record_generate() {
  local stream=$1 role=$2 model=$3 ctx=$4
  python3 - "$stream" "$role" "$model" "$ctx" "$samples" <<'PY'
import json
import sys

stream, role, model, ctx, dest = sys.argv[1:6]
error = None
pec = pens = ec = ens = 0
try:
    last = None
    for line in open(stream, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            continue
        if item.get("error") and last is None:
            error = str(item["error"])
        if item.get("done") is True:
            last = item
    if last is None and error is None:
        error = "ollama generate did not finish"
    if last is not None:
        pec = int(last.get("prompt_eval_count") or 0)
        pens = int(last.get("prompt_eval_duration") or 0)
        ec = int(last.get("eval_count") or 0)
        ens = int(last.get("eval_duration") or 0)
except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
    error = str(exc)
sample = {
    "role": role,
    "model": model,
    "kind": "generate",
    "requested_ctx": int(ctx),
    "prompt_eval_count": pec,
    "prompt_eval_ns": pens,
    "eval_count": ec,
    "eval_ns": ens,
    "dimension": None,
    "error": error,
}
with open(dest, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sample) + "\n")
PY
}

pull_model() {
  local model=$1
  if [ "$SKIP_PULL" = "1" ]; then
    return 0
  fi
  python3 - "$model" "$tmp/pull.json" <<'PY'
import json
import sys

with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump({"name": sys.argv[1], "stream": False}, handle)
PY
  if ! curl -sS --max-time "$PULL_MAX_TIME" \
    -o "$tmp/pull.out" \
    -H 'Content-Type: application/json' \
    --data-binary @"$tmp/pull.json" \
    "$OLLAMA_HOST/api/pull"; then
    return 1
  fi
  python3 - "$tmp/pull.out" <<'PY'
import json
import sys

item = json.loads(open(sys.argv[1], encoding="utf-8").read() or "{}")
if isinstance(item, dict) and item.get("error"):
    raise SystemExit(str(item["error"]))
PY
}

measure_generate() {
  local role=$1 model=$2 ctx=$3
  python3 - "$model" "$ctx" "$PREDICT" "$tmp/prompt.txt" "$tmp/payload.json" <<'PY'
import json
import sys

model, ctx, predict, prompt_path, out = sys.argv[1:6]
chars = int(ctx) * 3
sentence = "The quick brown fox jumps over the lazy dog. "
prompt = (sentence * ((chars // len(sentence)) + 1))[:chars]
with open(prompt_path, "w", encoding="utf-8") as handle:
    handle.write(prompt)
body = {
    "model": model,
    "prompt": prompt,
    "stream": True,
    "keep_alive": "5m",
    "options": {"num_ctx": int(ctx), "num_predict": int(predict), "temperature": 0},
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(body, handle)
PY
  if ! curl -sS --max-time "$MAX_TIME" \
    -o "$tmp/stream.ndjson" \
    -H 'Content-Type: application/json' \
    --data-binary @"$tmp/payload.json" \
    "$OLLAMA_HOST/api/generate"; then
    append_sample "$role" "$model" generate "$ctx" 0 0 0 0 null "request failed"
    return 0
  fi
  record_generate "$tmp/stream.ndjson" "$role" "$model" "$ctx"
}

measure_vision() {
  local model=$1
  python3 - "$model" "$PREDICT" "$tmp/vision.json" <<'PY'
import json
import sys

png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
body = {
    "model": sys.argv[1],
    "stream": True,
    "keep_alive": "5m",
    "messages": [
        {
            "role": "user",
            "content": "Describe this image in one sentence.",
            "images": [png],
        }
    ],
    "options": {"num_predict": int(sys.argv[2]), "temperature": 0},
}
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    json.dump(body, handle)
PY
  if ! curl -sS --max-time "$MAX_TIME" \
    -o "$tmp/stream.ndjson" \
    -H 'Content-Type: application/json' \
    --data-binary @"$tmp/vision.json" \
    "$OLLAMA_HOST/api/chat"; then
    append_sample vision "$model" generate 0 0 0 0 0 null "request failed"
    return 0
  fi
  record_generate "$tmp/stream.ndjson" vision "$model" 0
}

measure_embed() {
  local model=$1 out="$tmp/embed.json" body="$tmp/embed-body.json" time_s
  python3 - "$model" "$body" <<'PY'
import json
import sys

body = {
    "model": sys.argv[1],
    "input": ["kubernetes on arm64", "slovak payroll", "typst resume"],
}
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(body, handle)
PY
  if ! time_s=$(curl -sS --max-time "$MAX_TIME" \
    -o "$out" \
    -w '%{time_total}' \
    -H 'Content-Type: application/json' \
    --data-binary @"$body" \
    "$OLLAMA_HOST/api/embed"); then
    append_sample embed "$model" embed 0 0 0 0 0 null "request failed"
    return 0
  fi
  python3 - "$out" "$time_s" "$model" "$samples" <<'PY'
import json
import sys

path, seconds, model, dest = sys.argv[1:5]
error = None
count = 0
dimension = None
try:
    item = json.loads(open(path, encoding="utf-8").read() or "{}")
    if not isinstance(item, dict):
        error = "embed response was not an object"
    elif item.get("error"):
        error = str(item["error"])
    else:
        rows = item.get("embeddings")
        if rows is None and isinstance(item.get("embedding"), list):
            rows = [item["embedding"]]
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
            error = "embed response had no vectors"
        else:
            count = len(rows)
            dimension = len(rows[0])
except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
    error = str(exc)
elapsed = int(float(seconds) * 1_000_000_000)
sample = {
    "role": "embed",
    "model": model,
    "kind": "embed",
    "requested_ctx": 0,
    "prompt_eval_count": 0,
    "prompt_eval_ns": 0,
    "eval_count": count,
    "eval_ns": elapsed,
    "dimension": dimension,
    "error": error,
}
with open(dest, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sample) + "\n")
PY
}

run_role_models() {
  local role=$1
  shift
  local model ctx
  for model in "$@"; do
    [ -n "$model" ] || continue
    if ! pull_model "$model"; then
      append_sample "$role" "$model" generate 0 0 0 0 0 null "pull failed"
      continue
    fi
    if [ "$role" = "vision" ]; then
      measure_vision "$model"
    elif [ "$role" = "embed" ]; then
      measure_embed "$model"
    else
      for ctx in $CONTEXTS; do
        measure_generate "$role" "$model" "$ctx"
      done
    fi
  done
}

if ! curl -fsS --connect-timeout 3 --max-time 10 "$OLLAMA_HOST/api/tags" -o "$tmp/tags.json"; then
  echo "Ollama did not answer at $OLLAMA_HOST. This run did not change $OUT_MD." >&2
  exit 1
fi

# shellcheck disable=SC2086
run_role_models fast $FAST
# shellcheck disable=SC2086
run_role_models heavy $HEAVY
# shellcheck disable=SC2086
run_role_models vision $VISION
# shellcheck disable=SC2086
run_role_models embed $EMBED

mkdir -p "$(dirname "$OUT_MD")" "$(dirname "$OUT_JSONL")"
cp "$samples" "$OUT_JSONL"
python3 -m app.services.benchmark_report \
  --samples "$samples" \
  --output "$OUT_MD" \
  --ran-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --host "$(hostname 2>/dev/null || echo unknown)" \
  --ollama-host "$OLLAMA_HOST" \
  --require-success
