"""Make API responses model-friendly: drop drawings and molfiles, then fit a size budget."""
from __future__ import annotations

import json
from typing import Any

# Drawings and molfiles are large (about 10 KB per compound) and useless to a language model.
DROP_KEYS = frozenset({
    "rendered_svg", "svg", "scaffold_svg", "original_molblock", "standardized_molblock", "molblock",
})


def strip_drawings(value: Any) -> Any:
    """Recursively remove drawing fields and any string that is an SVG document."""
    if isinstance(value, dict):
        return {
            key: strip_drawings(item)
            for key, item in value.items()
            if key not in DROP_KEYS and not (isinstance(item, str) and item.lstrip().startswith("<svg"))
        }
    if isinstance(value, list):
        return [strip_drawings(item) for item in value]
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _lists(value: Any, path: tuple = ()):
    """Yield (path, list) for every list of more than one item."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _lists(item, path + (key,))
    elif isinstance(value, list):
        if len(value) > 1:
            yield path, value
        for index, item in enumerate(value[:50]):
            yield from _lists(item, path + (index,))


def _replace(root: Any, path: tuple, new: list) -> None:
    node = root
    for step in path[:-1]:
        node = node[step]
    if path:
        node[path[-1]] = new


def fit(value: Any, limit: int) -> tuple[Any, list[dict]]:
    """Shrink the longest lists (halving) until the JSON fits ``limit`` characters.

    Returns the possibly reduced value and a note for every list that was cut, so the model
    is told exactly what it is not seeing instead of silently reasoning from a partial list.
    """
    notes: list[dict] = []
    if len(_dump(value)) <= limit:
        return value, notes
    # Work on a copy inside a holder, so the caller's object is untouched and even a top-level
    # list has a non-empty path to replace.
    holder = {"v": json.loads(_dump(value))}
    for _ in range(400):
        if len(_dump(holder)) - 6 <= limit:
            break
        candidates = list(_lists(holder))
        if not candidates:
            break
        path, items = max(candidates, key=lambda pair: len(_dump(pair[1])))
        keep = max(1, len(items) // 2)
        label = [str(step) for step in path[1:]]
        total = next((note["total"] for note in notes if note["path"] == label), len(items))
        _replace(holder, path, items[:keep])
        notes = [note for note in notes if note["path"] != label]
        notes.append({"path": label, "shown": keep, "total": total})
    return holder["v"], notes
