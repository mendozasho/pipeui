from __future__ import annotations

from pipeui.validation.column import ColumnRegistryEntry
from pipeui.validation.source import SourceRegistryEntry


class FailedRegistryEntry:
    def __init__(self):
        self.failures: list[tuple[SourceRegistryEntry | ColumnRegistryEntry, str]] = []

    def add(self, entry: SourceRegistryEntry | ColumnRegistryEntry, reason: str) -> None:
        self.failures.append((entry, reason))

    def has_failures(self) -> bool:
        return bool(self.failures)

    def __repr__(self) -> str:
        return f"FailedRegistryEntry(failures={self.failures!r})"


class FailedFunctionEntry:
    def __init__(self):
        self.failures: list[tuple[object, str]] = []

    def add(self, obj: object, reason: str) -> None:
        self.failures.append((obj, reason))

    def has_failures(self) -> bool:
        return bool(self.failures)

    def __repr__(self) -> str:
        return f"FailedFunctionEntry(failures={self.failures!r})"
