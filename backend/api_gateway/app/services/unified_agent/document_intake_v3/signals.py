"""Document Intake V3 — Signal and ClassificationResult dataclasses.

Signal carries one piece of evidence (from OCR, caption, or DB) with positive
and negative contributions to TransferType scores. Classifier aggregates Signals
to pick a winning type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .transfer_types import TransferType


SignalSource = Literal["ocr", "caption", "db", "forced"]


@dataclass
class Signal:
    """One piece of evidence contributing to transfer type classification."""

    source: SignalSource
    name: str
    strength: float
    targets: dict[TransferType, float] = field(default_factory=dict)
    excludes: dict[TransferType, float] = field(default_factory=dict)

    def to_telemetry(self) -> dict:
        """PII-safe compact representation for telemetry logging."""
        return {
            "src": self.source,
            "name": self.name,
            "strength": round(self.strength, 3),
            "targets": {t.value: round(w, 3) for t, w in self.targets.items()},
            "excludes": {t.value: round(w, 3) for t, w in self.excludes.items()},
        }


@dataclass
class ClassificationResult:
    """Output of TransferClassifier.classify()."""

    type: TransferType
    confidence: float
    alternatives: list[tuple[TransferType, float]] = field(default_factory=list)
    signals_fired: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
