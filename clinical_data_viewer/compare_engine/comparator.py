from __future__ import annotations

from .models import MatchVariable, SourceRecord
from .normalize import values_equal


def differing_variables(
    main: SourceRecord,
    qc: SourceRecord,
    common_kinds: dict[str, str],
    key_variables: tuple[str, ...],
    match_variables: tuple[MatchVariable, ...],
) -> tuple[str, ...]:
    tolerance_by_name = {
        variable.name: variable.tolerance for variable in match_variables
    }

    def differs(name: str) -> bool:
        return not values_equal(
            main.values.get(name),
            qc.values.get(name),
            common_kinds[name],
            tolerance_by_name.get(name, 0.0),
        )

    if key_variables:
        key_differences = tuple(name for name in key_variables if differs(name))
        if key_differences:
            return key_differences
        return tuple(
            name for name in common_kinds if name not in key_variables and differs(name)
        )
    return tuple(name for name in common_kinds if differs(name))
