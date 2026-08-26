from __future__ import annotations

"""Small shared predicates for dataset lifecycle and analysis eligibility."""

ANALYSIS_DATASET_KINDS = frozenset({"sas", "merge"})


def is_analysis_dataset(handle_or_kind: object) -> bool:
    """Return whether a dataset may feed the normal analysis modules."""

    kind = getattr(handle_or_kind, "kind", handle_or_kind)
    return kind in ANALYSIS_DATASET_KINDS
