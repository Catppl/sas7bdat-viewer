from __future__ import annotations

from typing import Any


class AnalysisModuleController:
    """Small module boundary while workflow implementations migrate incrementally.

    The owner remains the compatibility facade for now.  Keeping this boundary
    explicit lets each workflow move without changing MainWindow's public API.
    """

    name: str = "analysis"

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self.owner, attribute)
