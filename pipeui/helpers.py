from __future__ import annotations

import re
from pathlib import Path


def infer_pattern(filename: str) -> str | None:
    """Return a generalized regex pattern for a filename, or None if no digits exist.

    Generally used to infer the filename of a new data source. For example, `sales-2025.04.03.xlsx`.
    """
    stem = Path(filename).stem
    if not re.search(r"\d", stem):
        return None
    return re.sub(r"\d+", r"\\d+", stem)
