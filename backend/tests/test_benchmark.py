from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.services.benchmark_report import (
    EMBED_DIMENSION,
    UNRUN_MARKDOWN,
    Sample,
    choose_role,
    load_samples,
    render_markdown,
)
from app.vector_store import VECTOR_SIZE

ROOT = Path(__file__).resolve().parents[2]


def _sample(**overrides: object) -> Sample:
    values: dict[str, object] = {
        "role": "heavy",
        "model": "gpt-oss:120b",
        "kind": "generate",
        "requested_ctx": 32768,
        "prompt_eval_count": 30000,
        "prompt_eval_ns": 5_000_000_000,
        "eval_count": 32,
        "eval_ns": 800_000_000,
        "dimension": None,
        "error": None,
    }
    values.update(overrides)
    return Sample(**values)  # type: ignore[arg-type]


def test_embed_dimension_matches_the_vector_store() -> None:
    assert EMBED_DIMENSION == VECTOR_SIZE


def test_committed_benchmark_page_is_the_unrun_report() -> None:
    text = (ROOT / "docs" / "benchmarks.md").read_text(encoding="utf-8")
    assert text == UNRUN_MARKDOWN
    assert "not run" in text


def test_role_selection_prefers_a_real_32k_heavy_sample() -> None:
    slow_32k = _sample(model="slow-32", eval_ns=2_000_000_000)
    fast_8k = _sample(
        model="fast-8",
        requested_ctx=8192,
        prompt_eval_count=8000,
        eval_ns=100_000_000,
    )
    choice = choose_role([slow_32k, fast_8k], "heavy")
    assert choice.model == "slow-32"
    fallback = choose_role([fast_8k], "heavy")
    assert fallback.model == "fast-8"
    assert "fallback" in fallback.reason
    short = _sample(model="short", prompt_eval_count=10)
    assert choose_role([short], "heavy").model is None


def test_fast_vision_and_embed_rules() -> None:
    fast = choose_role(
        [
            _sample(
                role="fast",
                model="a",
                requested_ctx=8192,
                prompt_eval_count=8000,
                eval_ns=200_000_000,
            ),
            _sample(
                role="fast",
                model="b",
                requested_ctx=8192,
                prompt_eval_count=8000,
                eval_ns=100_000_000,
            ),
            _sample(
                role="fast",
                model="c",
                requested_ctx=8192,
                prompt_eval_count=100,
                eval_ns=1_000_000,
            ),
        ],
        "fast",
    )
    assert fast.model == "b"
    vision = choose_role(
        [_sample(role="vision", model="qwen3-vl:8b", requested_ctx=0, prompt_eval_count=0)],
        "vision",
    )
    assert vision.model == "qwen3-vl:8b"
    embed = choose_role(
        [
            Sample(
                role="embed",
                model="other",
                kind="embed",
                requested_ctx=0,
                eval_count=3,
                eval_ns=50_000_000,
                dimension=512,
            ),
            Sample(
                role="embed",
                model="bge-m3",
                kind="embed",
                requested_ctx=0,
                eval_count=3,
                eval_ns=80_000_000,
                dimension=1024,
            ),
        ],
        "embed",
    )
    assert embed.model == "bge-m3"


def test_render_includes_the_suggestion_and_does_not_claim_a_config_edit(tmp_path: Path) -> None:
    text = render_markdown(
        [_sample(role="fast", model="qwen3:30b-a3b", requested_ctx=8192, prompt_eval_count=8000)],
        ran_at="2026-10-28T03:15:00Z",
        host="spark",
        ollama_host="http://ollama:11434",
    )
    assert "SPARKY_MODEL_FAST=qwen3:30b-a3b" in text
    assert "does not edit `config.py`" in text
    assert "2026-10-28T03:15:00Z" in text
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps(
            {
                "role": "embed",
                "model": "bge-m3",
                "kind": "embed",
                "requested_ctx": 0,
                "prompt_eval_count": 0,
                "prompt_eval_ns": 0,
                "eval_count": 3,
                "eval_ns": 10,
                "dimension": 1024,
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_samples(path)
    assert loaded[0].model == "bge-m3"


def test_benchmark_script_against_a_fake_ollama(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._json({"models": []})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8") or "{}")
            if self.path == "/api/pull":
                self._json({"status": "success"})
                return
            if self.path == "/api/embed":
                self._json({"embeddings": [[0.0] * 1024, [0.0] * 1024, [0.0] * 1024]})
                return
            ctx = int((body.get("options") or {}).get("num_ctx") or 0)
            prompt_tokens = 8000 if ctx == 8192 else 30000 if ctx == 32768 else 12
            duration = 400_000_000 if ctx == 8192 else 800_000_000
            done = {
                "done": True,
                "prompt_eval_count": prompt_tokens,
                "prompt_eval_duration": 2_000_000_000,
                "eval_count": 32,
                "eval_duration": duration,
            }
            payload = (
                json.dumps({"response": "ok", "done": False}) + "\n" + json.dumps(done) + "\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, obj: object) -> None:
            raw = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
    )
    thread.start()
    out = tmp_path / "benchmarks.md"
    try:
        port = server.server_address[1]
        env = os.environ.copy()
        env.update(
            {
                "OLLAMA_HOST": f"http://127.0.0.1:{port}",
                "SPARKY_BENCH_MARKDOWN": str(out),
                "SPARKY_BENCH_SAMPLES": str(tmp_path / "benchmarks.jsonl"),
                "SPARKY_BENCH_FAST": "fast-test",
                "SPARKY_BENCH_HEAVY": "heavy-test",
                "SPARKY_BENCH_VISION": "vision-test",
                "SPARKY_BENCH_EMBED": "embed-test",
                "SPARKY_BENCH_MAX_TIME": "10",
                "SPARKY_BENCH_PULL_MAX_TIME": "10",
            }
        )
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "benchmark_models.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "SPARKY_MODEL_FAST=fast-test" in text
    assert "SPARKY_MODEL_HEAVY=heavy-test" in text
    assert "SPARKY_MODEL_VISION=vision-test" in text
    assert "SPARKY_MODEL_EMBED=embed-test" in text
    assert "80.0" in text


def test_benchmark_script_does_not_touch_the_report_when_ollama_is_down(tmp_path: Path) -> None:
    out = tmp_path / "benchmarks.md"
    env = os.environ.copy()
    env.update(
        {
            "OLLAMA_HOST": "http://127.0.0.1:9",
            "SPARKY_BENCH_MARKDOWN": str(out),
            "SPARKY_BENCH_SAMPLES": str(tmp_path / "benchmarks.jsonl"),
        }
    )
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "benchmark_models.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert not out.exists()
    assert "did not change" in result.stderr
