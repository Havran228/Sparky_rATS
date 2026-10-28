"""Lexical matching, alias expansion, citation checks, and the deterministic score.

The lexical layer is what a corporate ATS can see: word-boundary hits over FTS5
chunks, expanded through the EN/SK alias dictionary (ADR-008). The score is
computed here. The model never emits a number.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.db.repositories.bundles import KnowledgeRepository
from app.services.parser import fold_diacritics

ALIAS_PATH = Path(__file__).resolve().parent.parent / "data" / "aliases_v1.json"
_GROUP_WEIGHT = {"mandatory": "mandatory", "preferred": "preferred", "offering": "offerings"}
_LIGHT_VALUE = {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}


@dataclass(frozen=True)
class AliasEntry:
    canonical: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class AliasDictionary:
    version: str
    entries: tuple[AliasEntry, ...]

    def forms(self, term: str) -> list[str]:
        folded = fold_diacritics(term)
        for entry in self.entries:
            keys = (entry.canonical, *entry.aliases)
            if any(fold_diacritics(key) == folded for key in keys):
                return _unique(list(keys))
        return [term.strip()] if term.strip() else []


@dataclass(frozen=True)
class Keyword:
    term: str
    group: str
    category: str


@dataclass(frozen=True)
class LexicalTerm:
    term: str
    group: str
    category: str
    aliases: list[str]
    hit_count: int
    document_ids: list[str]
    chunk_ids: list[str]
    filter_hazard: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "term": self.term,
            "group": self.group,
            "category": self.category,
            "aliases": self.aliases,
            "hit_count": self.hit_count,
            "document_ids": self.document_ids,
            "chunk_ids": self.chunk_ids,
            "filter_hazard": self.filter_hazard,
        }


@dataclass(frozen=True)
class LexicalReport:
    alias_version: str
    terms: list[LexicalTerm]

    def as_dict(self) -> dict[str, object]:
        return {
            "alias_version": self.alias_version,
            "terms": [term.as_dict() for term in self.terms],
        }


@dataclass(frozen=True)
class ClassifiedItem:
    term: str
    group: str
    category: str
    light: str
    rationale: str
    chunk_ids: list[str]
    citation_downgraded: bool
    dropped_chunk_ids: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "term": self.term,
            "group": self.group,
            "category": self.category,
            "light": self.light,
            "rationale": self.rationale,
            "chunk_ids": self.chunk_ids,
            "citation_downgraded": self.citation_downgraded,
            "dropped_chunk_ids": self.dropped_chunk_ids,
        }


def load_aliases(path: Path | None = None) -> AliasDictionary:
    source = path or ALIAS_PATH
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("alias dictionary must be an object")
    version = raw.get("version")
    entries_raw = raw.get("entries")
    if not isinstance(version, str) or not version:
        raise ValueError("alias dictionary needs a version")
    if not isinstance(entries_raw, list):
        raise ValueError("alias dictionary needs entries")
    entries: list[AliasEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        canonical = item.get("canonical")
        aliases = item.get("aliases")
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        alias_list = aliases if isinstance(aliases, list) else []
        forms = [alias.strip() for alias in alias_list if isinstance(alias, str) and alias.strip()]
        entries.append(AliasEntry(canonical=canonical.strip(), aliases=tuple(forms)))
    return AliasDictionary(version=version, entries=tuple(entries))


def lexical_report(
    keywords: list[Keyword],
    *,
    bundle_ids: list[str],
    repository: KnowledgeRepository,
    aliases: AliasDictionary | None = None,
) -> LexicalReport:
    dictionary = aliases or load_aliases()
    terms: list[LexicalTerm] = []
    for keyword in keywords:
        forms = dictionary.forms(keyword.term)
        hits = repository.search_fts(fts_query(forms), bundle_ids) if forms else []
        hit_count = 0
        documents: list[str] = []
        chunks: list[str] = []
        seen_docs: set[str] = set()
        for hit in hits:
            count = count_forms(hit.content, forms)
            if count <= 0:
                continue
            hit_count += count
            chunks.append(hit.id)
            if hit.document_id not in seen_docs:
                seen_docs.add(hit.document_id)
                documents.append(hit.document_id)
        hazard = keyword.group == "mandatory" and hit_count == 0
        terms.append(
            LexicalTerm(
                term=keyword.term,
                group=keyword.group,
                category=keyword.category,
                aliases=[
                    form
                    for form in forms
                    if fold_diacritics(form) != fold_diacritics(keyword.term)
                ],
                hit_count=hit_count,
                document_ids=documents,
                chunk_ids=chunks,
                filter_hazard=hazard,
            )
        )
    return LexicalReport(alias_version=dictionary.version, terms=terms)


def count_forms(content: str, forms: list[str]) -> int:
    folded = fold_diacritics(content)
    occupied: list[tuple[int, int]] = []
    total = 0
    for form in _unique(forms):
        pattern = re.compile(rf"(?<!\w){re.escape(fold_diacritics(form))}(?!\w)")
        for match in pattern.finditer(folded):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            occupied.append(span)
            total += 1
    return total


def verify_citation(
    *,
    term: str,
    group: str,
    category: str,
    light: str,
    rationale: str,
    chunk_ids: list[str],
    allowed_ids: set[str],
) -> ClassifiedItem:
    kept = [chunk_id for chunk_id in _unique(chunk_ids) if chunk_id in allowed_ids]
    dropped = [chunk_id for chunk_id in _unique(chunk_ids) if chunk_id not in allowed_ids]
    final = light
    note = rationale.strip()
    downgraded = False
    if light == "GREEN" and not kept:
        final = "YELLOW"
        downgraded = True
        suffix = "Downgraded to YELLOW: GREEN had no citation in the retrieved set."
        note = f"{note} {suffix}".strip()
    return ClassifiedItem(
        term=term,
        group=group,
        category=category,
        light=final,
        rationale=note,
        chunk_ids=kept,
        citation_downgraded=downgraded,
        dropped_chunk_ids=dropped,
    )


def match_percentage(items: list[ClassifiedItem], weights: dict[str, float]) -> float:
    """Weighted mean of group averages, as a percentage.

    A group with no items is left out and the remaining configured weights are
    renormalized for this run. The stored weights stay the configured ones.
    """
    grouped: dict[str, list[float]] = {"mandatory": [], "preferred": [], "offering": []}
    for item in items:
        value = _LIGHT_VALUE.get(item.light)
        if value is None or item.group not in grouped:
            continue
        grouped[item.group].append(value)
    used = {name: values for name, values in grouped.items() if values}
    if not used:
        return 0.0
    weight_sum = 0.0
    for name in used:
        weight_sum += float(weights.get(_GROUP_WEIGHT[name], 0.0))
    if weight_sum <= 0:
        return 0.0
    total = 0.0
    for name, values in used.items():
        average = sum(values) / len(values)
        total += (float(weights.get(_GROUP_WEIGHT[name], 0.0)) / weight_sum) * average
    return round(total * 100, 2)


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = 60, limit: int = 8) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        rank = 0
        for item in ranking:
            if item in seen:
                continue
            seen.add(item)
            rank += 1
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores, key=lambda item: (-scores[item], item))
    return ordered[:limit]


def fts_query(forms: list[str]) -> str:
    parts: list[str] = []
    for form in _unique(forms):
        cleaned = " ".join(form.replace('"', " ").split())
        if cleaned:
            parts.append(f'"{cleaned}"')
    return " OR ".join(parts)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        marker = fold_diacritics(value)
        if not marker or marker in seen:
            continue
        seen.add(marker)
        unique.append(value)
    return unique
