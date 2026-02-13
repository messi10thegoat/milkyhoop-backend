from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import asyncpg


@dataclass
class ValidationContext:
    """Shared context passed through all validation layers."""
    tenant_id: str
    user_id: str
    action_id: str
    action_type: int  # proto enum value
    category: int
    payload: dict  # parsed from draft_payload_json
    idempotency_key: str
    risk_level: int
    confidence: float
    pool: asyncpg.Pool
    errors: List[Dict[str, Any]] = field(default_factory=list)
    # Dry run results stored here by DryRunValidator
    journal_entries: List[Dict[str, Any]] = field(default_factory=list)
    total_debit: float = 0.0
    total_credit: float = 0.0
    balanced: bool = True
    impact_summary: Dict[str, str] = field(default_factory=dict)
    # Policy results
    requires_confirmation: bool = False
    confirmation_message: str = ""
    final_risk_level: Optional[int] = None

    def add_error(self, layer: str, code: str, message: str, blocking: bool = True, field_name: str = ""):
        self.errors.append({
            "layer": layer,
            "code": code,
            "message": message,
            "blocking": blocking,
            "field": field_name,
        })

    def add_warning(self, layer: str, code: str, message: str, field_name: str = ""):
        self.errors.append({
            "layer": layer,
            "code": code,
            "message": message,
            "blocking": False,
            "field": field_name,
        })

    def has_blocking_errors(self) -> bool:
        return any(e["blocking"] for e in self.errors)


class BaseValidator:
    """Abstract base class for all validation layers."""

    async def validate(self, ctx: ValidationContext) -> None:
        raise NotImplementedError
