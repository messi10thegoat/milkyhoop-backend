from fastapi import APIRouter

from ..services.db_pool import get_db_pool

router = APIRouter()


@router.get("/healthz")
async def health_check():
    return {"status": "ok"}


@router.get("/pool-stats")
async def pool_stats():
    """Read-only DB connection pool utilization metric (ops use).

    Exposed publicly (no auth) like /healthz, consistent with other
    read-only ops endpoints. Returns only pool sizing, no data.
    """
    pool = await get_db_pool()
    size = pool.get_size()
    idle = pool.get_idle_size()
    return {
        "status": "ok",
        "min": pool.get_min_size(),
        "max": pool.get_max_size(),
        "size": size,
        "used": size - idle,
        "free": idle,
        "idle": idle,
    }
