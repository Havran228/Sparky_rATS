from __future__ import annotations

from evals.runner import run_cases

from app.services.ats_engine import verify_citation


def test_harness_agrees_with_the_lexical_proxy() -> None:
    report = run_cases()
    assert report["model_name"] == "offline-lexical"
    assert report["prompt_version"] == "classify_match_v1"
    assert report["agreement"] == 1.0
    cases = {item["id"]: item for item in report["cases"]}
    assert cases["platform-engineer"]["filter_hazards"] == ["Go"]
    assert cases["platform-engineer"]["actual"]["Kubernetes"] == "GREEN"
    assert cases["slovak-language"]["actual"]["Angličtina"] == "GREEN"
    assert cases["slovak-language"]["filter_hazards"] == []


def test_harness_downgrades_a_green_without_a_real_citation() -> None:
    def invent(term: str, *, hit_count: int, chunk_ids: list[str]) -> tuple[str, list[str]]:
        del hit_count, chunk_ids
        return "GREEN", ["not-a-chunk"]

    report = run_cases(model_name="bad-citer", classifier=invent)
    actual = {item["id"]: item["actual"] for item in report["cases"]}
    assert actual["platform-engineer"]["Kubernetes"] == "YELLOW"
    assert report["agreement"] < 1.0
    sample = verify_citation(
        term="Kubernetes",
        group="mandatory",
        category="technology",
        light="GREEN",
        rationale="",
        chunk_ids=["not-a-chunk"],
        allowed_ids=set(),
    )
    assert sample.citation_downgraded is True
