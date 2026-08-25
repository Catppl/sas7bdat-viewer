from __future__ import annotations

import math


def normalize_missing(value: object, kind: str) -> object | None:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if kind == "character" and value == "":
        return None
    return value


def values_equal(
    main_value: object,
    qc_value: object,
    kind: str,
    tolerance: float = 0.0,
) -> bool:
    main = normalize_missing(main_value, kind)
    qc = normalize_missing(qc_value, kind)
    if main is None or qc is None:
        return main is None and qc is None
    if kind == "numeric":
        try:
            return abs(float(main) - float(qc)) <= tolerance
        except (TypeError, ValueError):
            return False
    return main == qc


def group_sort_key(values: tuple[object | None, ...], kinds: tuple[str, ...]):
    normalized = []
    for value, kind in zip(values, kinds, strict=True):
        if value is None:
            normalized.append((0, 0.0 if kind == "numeric" else ""))
        elif kind == "numeric":
            normalized.append((1, float(value)))
        else:
            normalized.append((1, str(value)))
    return tuple(normalized)
