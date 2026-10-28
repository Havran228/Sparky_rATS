"""Print agreement for the bundled cases. No model and no network."""

from __future__ import annotations

import json

from evals.runner import run_cases


def main() -> None:
    print(json.dumps(run_cases(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
