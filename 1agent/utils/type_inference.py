"""Shared type-inference helpers.

#21: Both the Excel parser (``_infer_dtype``) and the chart engine
(``_is_numeric``) classify a column as numeric when a sufficient fraction
of its non-null values parse as ``float``. Previously the two used
different thresholds (70% vs 95%) so the same column could be classified
differently depending on which subsystem was looking — producing charts
where numeric values were silently dropped or text columns were treated
as numeric.
"""
from __future__ import annotations

from typing import Iterable, Optional

#: Single source of truth for numeric classification (#21).
NUMERIC_THRESHOLD = 0.95


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_numeric_column(values: Iterable, threshold: float = NUMERIC_THRESHOLD) -> bool:
    """Return True if >= ``threshold`` of ``values`` parse as a float.

    Empty / all-null input returns False.
    """
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return False
    hits = sum(1 for v in non_null if _to_float(v) is not None)
    return hits >= threshold * len(non_null)


def infer_dtype(samples: Iterable, threshold: float = NUMERIC_THRESHOLD) -> str:
    """Same logic as the legacy ``excel_parser._infer_dtype`` but using the
    shared ``is_numeric_column`` threshold."""
    samples = list(samples)
    if is_numeric_column(samples, threshold=threshold):
        return "numeric"
    return "string"
