"""Structural diff of two JSON values (ADR-015)."""

from __future__ import annotations


def json_diff(left: object, right: object) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    _walk(left, right, "", changes)
    return changes


def _walk(left: object, right: object, path: str, changes: list[dict[str, object]]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        seen: set[str] = set()
        keys: list[str] = []
        for key in [*left, *right]:
            name = str(key)
            if name in seen:
                continue
            seen.add(name)
            keys.append(name)
        for key in keys:
            child = f"{path}/{key}"
            if key not in left:
                changes.append({"path": child, "kind": "added", "right": right[key]})
            elif key not in right:
                changes.append({"path": child, "kind": "removed", "left": left[key]})
            else:
                _walk(left[key], right[key], child, changes)
        return
    if isinstance(left, list) and isinstance(right, list):
        shared = min(len(left), len(right))
        for index in range(shared):
            _walk(left[index], right[index], f"{path}/{index}", changes)
        for index in range(shared, len(left)):
            changes.append({"path": f"{path}/{index}", "kind": "removed", "left": left[index]})
        for index in range(shared, len(right)):
            changes.append({"path": f"{path}/{index}", "kind": "added", "right": right[index]})
        return
    if left != right:
        changes.append(
            {"path": path or "/", "kind": "changed", "left": left, "right": right}
        )
