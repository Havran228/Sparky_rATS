"""Turn Ollama benchmark samples into docs/benchmarks.md (ADR-002).

The script that talks to Ollama lives in scripts/benchmark_models.sh. This
module only parses measurements and chooses role defaults. It never calls a
model and never edits config.py.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

EMBED_DIMENSION = 1024
PROMPT_FRACTION = 0.5

UNRUN_MARKDOWN = """\
# Model benchmark

Status: not run.

Role defaults in `backend/app/config.py` are still the ADR-002 candidates
(`qwen3:30b-a3b`, `gpt-oss:120b`, `qwen3-vl:8b`, `bge-m3`).
Nothing in this file is a measurement.

Run `scripts/benchmark_models.sh` on the Spark. The procedure is in
[the runbook](runbook.md). The script overwrites this file only after Ollama
answers. It does not edit `config.py`.

ADR-002 stays Proposed until this file contains a Spark run and the owner
records acceptance.
"""

_ROLE_ORDER = {"fast": 0, "heavy": 1, "vision": 2, "embed": 3}


@dataclass(frozen=True)
class Sample:
    role: str
    model: str
    kind: str
    requested_ctx: int
    prompt_eval_count: int = 0
    prompt_eval_ns: int = 0
    eval_count: int = 0
    eval_ns: int = 0
    dimension: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class Choice:
    role: str
    model: str | None
    reason: str


def decode_tok_s(sample: Sample) -> float | None:
    if sample.error or sample.kind != "generate":
        return None
    if sample.eval_count <= 0 or sample.eval_ns <= 0:
        return None
    return sample.eval_count / (sample.eval_ns / 1_000_000_000)


def ttft_seconds(sample: Sample) -> float | None:
    if sample.error or sample.prompt_eval_ns <= 0:
        return None
    return sample.prompt_eval_ns / 1_000_000_000


def context_met(sample: Sample) -> bool:
    if sample.requested_ctx <= 0:
        return False
    return sample.prompt_eval_count >= int(sample.requested_ctx * PROMPT_FRACTION)


def _at_context(sample: Sample, ctx: int) -> bool:
    return (
        sample.requested_ctx == ctx
        and context_met(sample)
        and decode_tok_s(sample) is not None
    )


def choose(samples: list[Sample]) -> list[Choice]:
    return [choose_role(samples, role) for role in ("fast", "heavy", "vision", "embed")]


def choose_role(samples: list[Sample], role: str) -> Choice:
    own = [sample for sample in samples if sample.role == role and sample.error is None]
    if role == "embed":
        ok = [
            sample
            for sample in own
            if sample.kind == "embed" and sample.dimension == EMBED_DIMENSION and sample.eval_ns > 0
        ]
        if not ok:
            return Choice(role, None, "No embed sample returned 1024 dimensions.")
        best = min(
            ok,
            key=lambda sample: (sample.eval_ns / max(sample.eval_count, 1), sample.model),
        )
        return Choice(role, best.model, "Lowest latency among 1024-d embeddings.")
    if role == "vision":
        ok = [sample for sample in own if decode_tok_s(sample) is not None]
        if not ok:
            return Choice(role, None, "No vision sample produced a decode rate.")
        return Choice(
            role,
            _fastest(ok).model,
            "Highest decode rate on the synthetic image prompt.",
        )
    if role == "fast":
        ok = [sample for sample in own if _at_context(sample, 8192)]
        if not ok:
            return Choice(role, None, "No fast sample covered at least half of an 8k context.")
        return Choice(
            role,
            _fastest(ok).model,
            "Highest 8k decode rate. 32k is recorded and is not required for this role.",
        )
    if role == "heavy":
        at_32 = [
            sample
            for sample in own
            if sample.requested_ctx == 32768
            and context_met(sample)
            and decode_tok_s(sample) is not None
        ]
        if at_32:
            return Choice(role, _fastest(at_32).model, "Highest decode rate with a 32k prefill.")
        at_8 = [sample for sample in own if _at_context(sample, 8192)]
        if at_8:
            return Choice(
                role,
                _fastest(at_8).model,
                "No 32k sample qualified. The 8k figure is a fallback, not a confirmation.",
            )
        return Choice(role, None, "No heavy sample covered at least half of the requested context.")
    return Choice(role, None, "Unknown role.")


def render_markdown(
    samples: list[Sample],
    *,
    ran_at: str | None = None,
    host: str | None = None,
    ollama_host: str | None = None,
) -> str:
    if not samples:
        return UNRUN_MARKDOWN
    ordered = sorted(
        samples,
        key=lambda sample: (
            _ROLE_ORDER.get(sample.role, 9),
            sample.model,
            sample.requested_ctx,
        ),
    )
    lines = [
        "# Model benchmark",
        "",
        f"Ran at: {ran_at or 'unknown'}",
        f"Host: {host or 'unknown'}",
        f"Ollama: {ollama_host or 'unknown'}",
        "",
        "These numbers come from Ollama. `prompt_eval_duration` is time to first token.",
        "Decode rate is `eval_count / eval_duration`. The prompt is synthetic.",
        "The script does not read `/data` and does not edit `config.py`.",
        "",
        "## Measurements",
        "",
        "| Role | Model | Context | Prompt | TTFT (s) | tok/s | Dims | Error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sample in ordered:
        lines.append(_row(sample))
    lines.extend(["", "## Suggested role defaults", ""])
    lines.append(
        "fast: highest 8k decode rate that prefilled at least half of 8192 tokens. "
        "heavy: the same rule at 32768, otherwise the best qualifying 8k sample. "
        "vision: highest decode rate on a one-pixel image. "
        "embed: lowest latency among 1024-d results. Ties keep the name that sorts first."
    )
    lines.append("")
    lines.append("```")
    for choice in choose(samples):
        env = f"SPARKY_MODEL_{choice.role.upper()}"
        if choice.model is None:
            lines.append(f"# {env} is unset. {choice.reason}")
        else:
            lines.append(f"{env}={choice.model}")
    lines.append("```")
    lines.append("")
    for choice in choose(samples):
        lines.append(f"- {choice.role}: {choice.reason}")
    lines.extend(
        [
            "",
            "Put any suggested lines in `.env` and recreate the backend container.",
            "Leave `config.py` unchanged unless a checkout without `.env` should use new defaults.",
            "ADR-002 stays Proposed until you accept it after reading this file.",
            "",
        ]
    )
    return "\n".join(lines)


def load_samples(path: Path) -> list[Sample]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("samples file must be a list")
        items = raw
    else:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    samples: list[Sample] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each sample must be an object")
        samples.append(_sample(item))
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Sparky_rATS benchmark report.")
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ran-at", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--ollama-host", default="")
    parser.add_argument("--require-success", action="store_true")
    args = parser.parse_args(argv)
    try:
        samples = load_samples(args.samples)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    text = render_markdown(
        samples,
        ran_at=args.ran_at or None,
        host=args.host or None,
        ollama_host=args.ollama_host or None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(args.output)
    if args.require_success and not any(sample.error is None for sample in samples):
        return 1
    return 0


def _fastest(samples: list[Sample]) -> Sample:
    best = samples[0]
    best_rate = decode_tok_s(best) or 0.0
    for sample in samples[1:]:
        rate = decode_tok_s(sample) or 0.0
        if rate > best_rate or (rate == best_rate and sample.model < best.model):
            best = sample
            best_rate = rate
    return best


def _row(sample: Sample) -> str:
    ctx = str(sample.requested_ctx) if sample.requested_ctx else "n/a"
    if sample.kind == "generate" and not sample.error:
        tokens = str(sample.prompt_eval_count)
    else:
        tokens = "n/a"
    ttft = ttft_seconds(sample)
    rate = decode_tok_s(sample)
    ttft_cell = f"{ttft:.2f}" if ttft is not None else "n/a"
    rate_cell = f"{rate:.1f}" if rate is not None else "n/a"
    dim = str(sample.dimension) if sample.dimension is not None else "n/a"
    error = _cell(sample.error or "")
    return " | ".join(
        (
            f"| {_cell(sample.role)}",
            _cell(sample.model),
            ctx,
            tokens,
            ttft_cell,
            rate_cell,
            dim,
            f"{error} |",
        )
    )


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


def _sample(item: dict[str, object]) -> Sample:
    role = _text(item.get("role"), "role")
    model = _text(item.get("model"), "model")
    kind = _text(item.get("kind"), "kind")
    if kind not in {"generate", "embed"}:
        raise ValueError("sample kind must be generate or embed")
    dimension = item.get("dimension")
    error = item.get("error")
    return Sample(
        role=role,
        model=model,
        kind=kind,
        requested_ctx=_number(item.get("requested_ctx")),
        prompt_eval_count=_number(item.get("prompt_eval_count")),
        prompt_eval_ns=_number(item.get("prompt_eval_ns")),
        eval_count=_number(item.get("eval_count")),
        eval_ns=_number(item.get("eval_ns")),
        dimension=None if dimension is None else _number(dimension),
        error=None if error is None else _text(error, "error"),
    )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"sample {label} must be a string")
    return value.strip()


def _number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("sample counts must be integers")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
