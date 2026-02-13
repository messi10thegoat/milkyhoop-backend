"""
action_executor gRPC Server

Async gRPC server implementing:
- PrepareAction: Create pending action with preview (Phase 1)
- ExecuteAction: Confirm and execute via kernel router (Phase 2)
- CancelAction: Cancel a pending action
- GetActionStatus: Query action status
- HealthCheck: Service health status

IRON LAW 0: All writes go through Kernel (API Gateway) — never direct DB writes.
IRON LAW 13: Optimistic locking prevents concurrent execution.
IRON LAW 14: Idempotency via idempotency_key.
"""
import asyncio
import json
import logging
import signal
from concurrent import futures
from datetime import datetime, timezone

import asyncpg
import grpc
from google.protobuf import timestamp_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc

from . import action_executor_pb2, action_executor_pb2_grpc, action_plan_pb2
from .config import settings
from .services.pending_action_store import PendingActionStore
from .services.kernel_router import KernelRouter
from .services.saga_service import SagaService

logger = logging.getLogger(__name__)

# Proto enum mapping
STATUS_MAP = {
    "PENDING": action_executor_pb2.PENDING,
    "EXECUTING": action_executor_pb2.EXECUTING,
    "COMPLETED": action_executor_pb2.COMPLETED,
    "FAILED": action_executor_pb2.FAILED,
    "CANCELLED": action_executor_pb2.CANCELLED,
    "EXPIRED": action_executor_pb2.EXPIRED,
}


def _to_timestamp(dt) -> timestamp_pb2.Timestamp:
    """Convert datetime to protobuf Timestamp."""
    ts = timestamp_pb2.Timestamp()
    if dt:
        if hasattr(dt, 'timestamp'):
            ts.FromDatetime(dt)
        else:
            ts.seconds = int(dt)
    return ts


class ActionExecutorServicer(action_executor_pb2_grpc.ActionExecutorServiceServicer):
    """gRPC servicer for ActionExecutorService."""

    def __init__(self, saga: SagaService):
        self.saga = saga
        logger.info("ActionExecutorServicer initialized")

    async def PrepareAction(self, request, context):
        """Phase 1: Create pending action with preview."""
        try:
            plan = request.action_plan
            payload = {}
            if plan.draft_payload_json:
                try:
                    payload = json.loads(plan.draft_payload_json)
                except json.JSONDecodeError:
                    payload = {}

            # Map proto enum to string for storage
            action_type_str = _action_type_to_string(plan.action_type)
            category_str = _category_to_string(plan.category)

            result = await self.saga.prepare(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                action_id=plan.action_id,
                action_type=action_type_str,
                category=category_str,
                draft_payload=payload,
                idempotency_key=plan.idempotency_key,
                confidence=plan.confidence,
                assumptions=list(plan.assumptions),
            )

            expires_ts = _to_timestamp(result.get("expires_at")) if result.get("expires_at") else None

            # Build preview message
            preview_msg = ""
            if result["success"]:
                preview_msg = f"Aksi {action_type_str} siap dieksekusi. Silakan konfirmasi."

            return action_executor_pb2.PrepareActionResponse(
                success=result["success"],
                pending_action_id=result.get("pending_action_id", ""),
                confirmation_token=result.get("confirmation_token", ""),
                preview_message=preview_msg,
                preview=None,  # Preview comes from validator's dry-run
                expires_at=expires_ts,
                error_message=result.get("error_message", ""),
            )

        except Exception as e:
            logger.error(f"PrepareAction error: {e}", exc_info=True)
            return action_executor_pb2.PrepareActionResponse(
                success=False,
                error_message=str(e),
            )

    async def ExecuteAction(self, request, context):
        """Phase 2: Execute confirmed action."""
        try:
            result = await self.saga.execute(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                pending_action_id=request.pending_action_id,
                confirmation_token=request.confirmation_token,
                idempotency_key=request.idempotency_key,
            )

            status_enum = STATUS_MAP.get(result.get("status", "FAILED"), action_executor_pb2.FAILED)

            action_result = None
            if result.get("result"):
                r = result["result"]
                completed_ts = _to_timestamp(datetime.now(timezone.utc))
                action_result = action_executor_pb2.ActionResult(
                    entity_id=r.get("entity_id", ""),
                    entity_number=r.get("entity_number", ""),
                    entity_type=r.get("entity_type", ""),
                    journal_entry_id=r.get("journal_entry_id", ""),
                    message=r.get("message", ""),
                    completed_at=completed_ts,
                )

            return action_executor_pb2.ExecuteActionResponse(
                success=result["success"],
                status=status_enum,
                result=action_result,
                error_message=result.get("error_message", ""),
                error_code=result.get("error_code", ""),
            )

        except Exception as e:
            logger.error(f"ExecuteAction error: {e}", exc_info=True)
            return action_executor_pb2.ExecuteActionResponse(
                success=False,
                status=action_executor_pb2.FAILED,
                error_message=str(e),
                error_code="INTERNAL_ERROR",
            )

    async def CancelAction(self, request, context):
        """Cancel a pending action."""
        try:
            result = await self.saga.cancel(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                pending_action_id=request.pending_action_id,
                reason=request.reason,
            )

            status_enum = STATUS_MAP.get(result.get("status", "FAILED"), action_executor_pb2.FAILED)

            return action_executor_pb2.CancelActionResponse(
                success=result["success"],
                status=status_enum,
                message=result.get("message", ""),
            )

        except Exception as e:
            logger.error(f"CancelAction error: {e}", exc_info=True)
            return action_executor_pb2.CancelActionResponse(
                success=False,
                status=action_executor_pb2.FAILED,
                message=str(e),
            )

    async def GetActionStatus(self, request, context):
        """Query action status."""
        try:
            result = await self.saga.get_status(
                tenant_id=request.tenant_id,
                action_id=request.action_id,
            )

            status_enum = STATUS_MAP.get(result.get("status", "PENDING"), action_executor_pb2.PENDING)

            return action_executor_pb2.GetActionStatusResponse(
                action_id=result.get("action_id", ""),
                status=status_enum,
                error_message=result.get("error_message", ""),
            )

        except Exception as e:
            logger.error(f"GetActionStatus error: {e}", exc_info=True)
            return action_executor_pb2.GetActionStatusResponse(
                action_id=request.action_id,
                status=action_executor_pb2.PENDING,
                error_message=str(e),
            )

    async def HealthCheck(self, request, context):
        """Service health check."""
        return action_plan_pb2.HealthResponse(
            status="healthy",
            timestamp=datetime.utcnow().isoformat() + "Z",
        )


class HealthServicer(health_pb2_grpc.HealthServicer):
    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING
        )


def _action_type_to_string(action_type: int) -> str:
    mapping = {
        0: "CREATE_CUSTOMER", 1: "UPDATE_CUSTOMER", 2: "CREATE_VENDOR", 3: "CREATE_PRODUCT",
        10: "CREATE_SALES_INVOICE", 11: "CREATE_PURCHASE_INVOICE", 12: "CREATE_EXPENSE",
        20: "RECEIVE_PAYMENT", 21: "MAKE_PAYMENT",
        30: "POST_GENERAL_JOURNAL", 31: "REVERSE_JOURNAL", 32: "CLOSE_PERIOD", 33: "REOPEN_PERIOD",
        40: "GET_BALANCE", 41: "GET_TRIAL_BALANCE", 42: "GET_AR_AGING",
    }
    return mapping.get(action_type, f"UNKNOWN_{action_type}")


def _category_to_string(category: int) -> str:
    mapping = {0: "MASTER_DATA", 1: "DOCUMENT", 2: "PAYMENT", 3: "ACCOUNTING", 4: "READ"}
    return mapping.get(category, "DOCUMENT")


async def _expiration_loop(store: PendingActionStore):
    """Background loop to expire old pending actions."""
    while True:
        try:
            await store.expire_old_actions()
        except Exception as e:
            logger.error(f"Expiration loop error: {e}")
        await asyncio.sleep(settings.EXPIRATION_CHECK_INTERVAL_SECONDS)


async def serve():
    # Create DB pool
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.DB_MIN_POOL,
        max_size=settings.DB_MAX_POOL,
    )
    logger.info(f"DB pool created: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")

    # Create services
    store = PendingActionStore(pool)
    router = KernelRouter()
    saga = SagaService(store, router)

    # Start expiration background task
    expiration_task = asyncio.create_task(_expiration_loop(store))

    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=settings.GRPC_MAX_WORKERS)
    )

    servicer = ActionExecutorServicer(saga)
    action_executor_pb2_grpc.add_ActionExecutorServiceServicer_to_server(servicer, server)

    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)

    logger.info(f"action_executor gRPC server starting on {listen_addr}")
    await server.start()
    logger.info("action_executor gRPC server STARTED")

    stop_event = asyncio.Event()

    def _signal_handler(signame):
        logger.info(f"Received {signame}, initiating graceful shutdown...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, sig.name)

    await stop_event.wait()

    logger.info("Shutting down gRPC server (5s grace period)...")
    expiration_task.cancel()
    await saga.close()
    await server.stop(5)
    await pool.close()
    logger.info("action_executor gRPC server stopped cleanly")


def main():
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing action_executor gRPC server...")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
