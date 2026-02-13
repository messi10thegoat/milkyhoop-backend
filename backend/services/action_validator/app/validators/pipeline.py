"""
Validation Pipeline
Orchestrates all 6 validation layers in order.
Implements fail-fast: stops on first blocking error.
"""
import logging
from typing import List

from .base import BaseValidator, ValidationContext
from .security_validator import SecurityValidator
from .accounting_validator import AccountingValidator
from .invariant_validator import InvariantValidator
from .idempotency_validator import IdempotencyValidator
from .dryrun_validator import DryRunValidator
from .policy_validator import PolicyValidator

logger = logging.getLogger(__name__)


class ValidationPipeline:
    """Runs all 6 validation layers in sequence with fail-fast behavior."""

    def __init__(self):
        self.validators: List[BaseValidator] = [
            SecurityValidator(),       # Layer 1: SECURITY
            AccountingValidator(),     # Layer 2: ACCOUNTING_RULES
            InvariantValidator(),      # Layer 3: INVARIANTS
            IdempotencyValidator(),    # Layer 4: IDEMPOTENCY
            DryRunValidator(),         # Layer 5: DRY_RUN
            PolicyValidator(),         # Layer 6: POLICY
        ]

    async def run(self, ctx: ValidationContext) -> ValidationContext:
        """Execute all validators in order. Stops on first blocking error."""
        for validator in self.validators:
            layer_name = validator.__class__.__name__
            logger.info(f"Running validation layer: {layer_name}")
            try:
                await validator.validate(ctx)
            except Exception as e:
                logger.error(f"Unexpected error in {layer_name}: {e}", exc_info=True)
                ctx.add_error(
                    layer=layer_name,
                    code="INTERNAL_ERROR",
                    message=f"Internal error in {layer_name}: {str(e)}",
                    blocking=True,
                )

            if ctx.has_blocking_errors():
                logger.info(f"Blocking error(s) found at {layer_name}, stopping pipeline")
                break

        return ctx

    async def run_dryrun_only(self, ctx: ValidationContext) -> ValidationContext:
        """Run only the DryRun validator (for DryRunAction RPC)."""
        dryrun = DryRunValidator()
        try:
            await dryrun.validate(ctx)
        except Exception as e:
            logger.error(f"Unexpected error in DryRunValidator: {e}", exc_info=True)
            ctx.add_error(
                layer="DryRunValidator",
                code="INTERNAL_ERROR",
                message=f"Internal error in dry run: {str(e)}",
                blocking=True,
            )
        return ctx
