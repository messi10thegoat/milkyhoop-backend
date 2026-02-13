"""
Layer 6: POLICY
Applies business policies:
- CRITICAL actions always require confirmation
- High amounts require extra confirmation
- Risk level escalation based on amount thresholds
"""
import logging

from .base import BaseValidator, ValidationContext
from ..config import settings

logger = logging.getLogger(__name__)

# ActionType enum values for critical actions
ACTION_TYPE_REVERSE_JOURNAL = 31
ACTION_TYPE_CLOSE_PERIOD = 32
ACTION_TYPE_REOPEN_PERIOD = 33

# RiskLevel enum values from proto
RISK_LOW = 0
RISK_MEDIUM = 1
RISK_HIGH = 2
RISK_CRITICAL = 3

# Critical action types that always require confirmation
CRITICAL_ACTIONS = {
    ACTION_TYPE_REVERSE_JOURNAL,
    ACTION_TYPE_CLOSE_PERIOD,
    ACTION_TYPE_REOPEN_PERIOD,
}

# Master data action types - always low risk, no confirmation needed
ACTION_TYPE_CREATE_CUSTOMER = 0
ACTION_TYPE_UPDATE_CUSTOMER = 1
ACTION_TYPE_CREATE_VENDOR = 2
ACTION_TYPE_CREATE_PRODUCT = 3
MASTER_DATA_ACTIONS = {
    ACTION_TYPE_CREATE_CUSTOMER,
    ACTION_TYPE_UPDATE_CUSTOMER,
    ACTION_TYPE_CREATE_VENDOR,
    ACTION_TYPE_CREATE_PRODUCT,
}


class PolicyValidator(BaseValidator):
    """Layer 6: Apply business policies for confirmation and risk assessment."""

    async def validate(self, ctx: ValidationContext) -> None:
        logger.debug("Running POLICY validation")
        payload = ctx.payload

        # Master data actions are always low risk, no confirmation needed
        if ctx.action_type in MASTER_DATA_ACTIONS:
            ctx.final_risk_level = RISK_LOW
            ctx.requires_confirmation = False
            ctx.confirmation_message = ""
            logger.debug(f"POLICY: master data action {ctx.action_type}, risk=LOW, no confirmation")
            return

        confirmation_reasons = []
        final_risk = ctx.risk_level

        # --- Critical actions always require confirmation ---
        if ctx.action_type in CRITICAL_ACTIONS:
            confirmation_reasons.append(
                f"Aksi kritis ({self._action_name(ctx.action_type)}) memerlukan konfirmasi"
            )
            if final_risk < RISK_HIGH:
                final_risk = RISK_HIGH

        # --- Amount-based policy ---
        amount = self._extract_amount(ctx)
        if amount > 0:
            if amount >= settings.CRITICAL_AMOUNT_THRESHOLD:
                confirmation_reasons.append(
                    f"Nominal sangat besar: Rp {amount:,.0f} (>= Rp {settings.CRITICAL_AMOUNT_THRESHOLD:,.0f})"
                )
                final_risk = RISK_CRITICAL
            elif amount >= settings.HIGH_AMOUNT_THRESHOLD:
                confirmation_reasons.append(
                    f"Nominal besar: Rp {amount:,.0f} (>= Rp {settings.HIGH_AMOUNT_THRESHOLD:,.0f})"
                )
                if final_risk < RISK_HIGH:
                    final_risk = RISK_HIGH

        # --- Low confidence policy ---
        if ctx.confidence > 0 and ctx.confidence < 0.7:
            confirmation_reasons.append(
                f"Tingkat keyakinan rendah: {ctx.confidence:.0%}"
            )
            if final_risk < RISK_MEDIUM:
                final_risk = RISK_MEDIUM

        # Set results
        if confirmation_reasons:
            ctx.requires_confirmation = True
            ctx.confirmation_message = " | ".join(confirmation_reasons)

        ctx.final_risk_level = final_risk

        logger.debug(
            f"POLICY: requires_confirmation={ctx.requires_confirmation}, "
            f"risk_level={final_risk}, reasons={confirmation_reasons}"
        )

    def _extract_amount(self, ctx: ValidationContext) -> float:
        """Extract the primary amount from payload or dry run results."""
        payload = ctx.payload

        # Try direct amount fields
        for key in ("amount", "total", "grand_total", "subtotal"):
            val = payload.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

        # Fall back to dry run total
        if ctx.total_debit > 0:
            return ctx.total_debit

        return 0.0

    def _action_name(self, action_type: int) -> str:
        names = {
            ACTION_TYPE_REVERSE_JOURNAL: "Reverse Journal",
            ACTION_TYPE_CLOSE_PERIOD: "Tutup Periode",
            ACTION_TYPE_REOPEN_PERIOD: "Buka Ulang Periode",
        }
        return names.get(action_type, f"ActionType({action_type})")
