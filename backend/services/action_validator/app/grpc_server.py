"""
action_validator gRPC Server

Async gRPC server implementing:
- ValidateAction: Full 6-layer validation pipeline
- DryRunAction: Journal preview only
- HealthCheck: Service health status

IRON LAW 0: This service ONLY validates. It NEVER writes accounting data.
"""
import asyncio
import json
import logging
import signal
from concurrent import futures
from datetime import datetime

import asyncpg
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from . import action_validator_pb2, action_validator_pb2_grpc, action_plan_pb2
from .config import settings
from .validators import ValidationPipeline, ValidationContext

logger = logging.getLogger(__name__)


class ActionValidatorServicer(action_validator_pb2_grpc.ActionValidatorServiceServicer):
    """gRPC servicer for ActionValidatorService."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.pipeline = ValidationPipeline()
        logger.info("ActionValidatorServicer initialized with DB pool")

    async def ValidateAction(self, request, context):
        """Full 6-layer validation pipeline."""
        try:
            tenant_id = request.tenant_id
            user_id = request.user_id
            plan = request.action_plan

            payload = {}
            if plan.draft_payload_json:
                try:
                    payload = json.loads(plan.draft_payload_json)
                except json.JSONDecodeError:
                    payload = {}

            ctx = ValidationContext(
                tenant_id=tenant_id,
                user_id=user_id,
                action_id=plan.action_id,
                action_type=plan.action_type,
                category=plan.category,
                payload=payload,
                idempotency_key=plan.idempotency_key,
                risk_level=0,
                confidence=plan.confidence,
                pool=self.pool,
            )

            ctx = await self.pipeline.run(ctx)

            # Build error list
            errors = []
            for e in ctx.errors:
                errors.append(action_validator_pb2.ValidationError(
                    layer=e["layer"],
                    code=e["code"],
                    message=e["message"],
                    blocking=e["blocking"],
                    field=e.get("field", ""),
                ))

            # Build dry run result
            dry_run = None
            if ctx.journal_entries:
                journal_entries = []
                for je in ctx.journal_entries:
                    journal_entries.append(action_validator_pb2.JournalPreview(
                        account_code=je.get("account_code", ""),
                        account_name=je.get("account_name", ""),
                        debit=je.get("debit", 0.0),
                        credit=je.get("credit", 0.0),
                        description=je.get("description", ""),
                    ))
                dry_run = action_validator_pb2.DryRunResult(
                    journal_entries=journal_entries,
                    total_debit=ctx.total_debit,
                    total_credit=ctx.total_credit,
                    balanced=ctx.balanced,
                    impact_summary=ctx.impact_summary,
                    currency="IDR",
                )

            valid = not ctx.has_blocking_errors()
            risk_level = ctx.final_risk_level if ctx.final_risk_level is not None else 0

            return action_validator_pb2.ValidateActionResponse(
                valid=valid,
                errors=errors,
                dry_run_result=dry_run,
                requires_confirmation=ctx.requires_confirmation,
                confirmation_message=ctx.confirmation_message,
                risk_level=risk_level,
            )

        except Exception as e:
            logger.error(f"ValidateAction error: {e}", exc_info=True)
            return action_validator_pb2.ValidateActionResponse(
                valid=False,
                errors=[action_validator_pb2.ValidationError(
                    layer="SYSTEM",
                    code="INTERNAL_ERROR",
                    message=str(e),
                    blocking=True,
                    field="",
                )],
                requires_confirmation=False,
                confirmation_message="",
                risk_level=0,
            )

    async def DryRunAction(self, request, context):
        """Dry-run only (journal preview without full validation)."""
        try:
            tenant_id = request.tenant_id
            user_id = request.user_id
            plan = request.action_plan

            payload = {}
            if plan.draft_payload_json:
                try:
                    payload = json.loads(plan.draft_payload_json)
                except json.JSONDecodeError:
                    payload = {}

            ctx = ValidationContext(
                tenant_id=tenant_id,
                user_id=user_id,
                action_id=plan.action_id or "",
                action_type=plan.action_type,
                category=plan.category if hasattr(plan, "category") else 0,
                payload=payload,
                idempotency_key="",
                risk_level=0,
                confidence=0.0,
                pool=self.pool,
            )

            ctx = await self.pipeline.run_dryrun_only(ctx)

            dry_run = None
            if ctx.journal_entries:
                journal_entries = []
                for je in ctx.journal_entries:
                    journal_entries.append(action_validator_pb2.JournalPreview(
                        account_code=je.get("account_code", ""),
                        account_name=je.get("account_name", ""),
                        debit=je.get("debit", 0.0),
                        credit=je.get("credit", 0.0),
                        description=je.get("description", ""),
                    ))
                dry_run = action_validator_pb2.DryRunResult(
                    journal_entries=journal_entries,
                    total_debit=ctx.total_debit,
                    total_credit=ctx.total_credit,
                    balanced=ctx.balanced,
                    impact_summary=ctx.impact_summary,
                    currency="IDR",
                )

            success = not ctx.has_blocking_errors()

            return action_validator_pb2.DryRunActionResponse(
                success=success,
                dry_run_result=dry_run,
                error_message="" if success else "; ".join(e["message"] for e in ctx.errors if e["blocking"]),
            )

        except Exception as e:
            logger.error(f"DryRunAction error: {e}", exc_info=True)
            return action_validator_pb2.DryRunActionResponse(
                success=False,
                dry_run_result=None,
                error_message=str(e),
            )

    async def HealthCheck(self, request, context):
        """Service health check."""
        try:
            await self.pool.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False

        status = "healthy" if db_ok else "degraded"
        return action_plan_pb2.HealthResponse(
            status=status,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )


class HealthServicer(health_pb2_grpc.HealthServicer):
    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING
        )


async def serve():
    # Create DB pool
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.DB_MIN_POOL,
        max_size=settings.DB_MAX_POOL,
    )
    logger.info(f"DB pool created: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")

    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=settings.GRPC_MAX_WORKERS)
    )

    servicer = ActionValidatorServicer(pool)
    action_validator_pb2_grpc.add_ActionValidatorServiceServicer_to_server(servicer, server)

    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)

    logger.info(f"action_validator gRPC server starting on {listen_addr}")
    await server.start()
    logger.info("action_validator gRPC server STARTED")

    stop_event = asyncio.Event()

    def _signal_handler(signame):
        logger.info(f"Received {signame}, initiating graceful shutdown...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, sig.name)

    await stop_event.wait()

    logger.info("Shutting down gRPC server (5s grace period)...")
    await server.stop(5)
    await pool.close()
    logger.info("action_validator gRPC server stopped cleanly")


def main():
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing action_validator gRPC server...")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
