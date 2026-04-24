"""Document Intake V3 — Handler Protocol + shared dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, Protocol, runtime_checkable

from ..transfer_types import TransferType


class HandlerMatchError(Exception):
    """Handler could not find a valid match candidate."""


class HandlerResolveError(Exception):
    """Handler found a match but could not build a resolved action."""


@dataclass
class ForcedOverride:
    type: Optional[TransferType] = None
    direction: Optional[str] = None  # V2 legacy: "in" | "out"
    counterparty_id: Optional[str] = None
    bank_account_id: Optional[str] = None


@dataclass
class HandlerMatchResult:
    success: bool
    candidates: list[Any] = field(default_factory=list)
    best: Optional[Any] = None
    needs_clarification: bool = False
    clarification_question: str = ""
    clarification_options: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@runtime_checkable
class TransferHandler(Protocol):
    transfer_type: ClassVar[TransferType]

    async def match(
        self,
        ocr: dict,
        caption: str,
        tenant_ctx: Any,
        forced: Optional[ForcedOverride] = None,
    ) -> HandlerMatchResult:
        ...

    async def resolve(
        self,
        match: HandlerMatchResult,
        ocr: dict,
        tenant_ctx: Any,
    ) -> Any:
        ...

    def describe_match(self, match: HandlerMatchResult) -> str:
        ...
