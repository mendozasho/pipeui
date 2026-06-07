from __future__ import annotations

from pipeui.validation.column import ColumnRegistryEntry
from pipeui.validation.source import SourceRegistryEntry


class FailedRegistryEntry:
    """
    A class to manage failed registry entries.

    Maintains a record of registry entries that failed to process, along with
    corresponding failure reasons. Provides functionality to add new failed
    entries, check if there are any failures, and represent the collected
    failures as a string.

    :ivar failures: A list of tuples where each tuple contains a failed
        registry entry and a string describing the reason for the failure.
    :type failures: list[tuple[SourceRegistryEntry | ColumnRegistryEntry, str]]
    """
    def __init__(self):
        self.failures: list[tuple[SourceRegistryEntry | ColumnRegistryEntry, str]] = []

    def add(self, entry: SourceRegistryEntry | ColumnRegistryEntry, reason: str) -> None:
        self.failures.append((entry, reason))

    def has_failures(self) -> bool:
        return bool(self.failures)

    def __repr__(self) -> str:
        return f"FailedRegistryEntry(failures={self.failures!r})"


class FailedFunctionEntry:
    """
    Represents a collection of function failures with associated details.

    This class is used to track and manage a collection of failed function
    executions. Each failure is stored as a tuple containing the object where
    the failure occurred and a string describing the reason for the failure.

    :ivar failures: A list of tuples representing failed function executions,
        where each tuple consists of an object and a failure reason.
    :type failures: list[tuple[object, str]]
    """
    def __init__(self):
        self.failures: list[tuple[object, str]] = []

    def add(self, obj: object, reason: str) -> None:
        self.failures.append((obj, reason))

    def has_failures(self) -> bool:
        return bool(self.failures)

    def __repr__(self) -> str:
        return f"FailedFunctionEntry(failures={self.failures!r})"
