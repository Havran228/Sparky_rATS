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
