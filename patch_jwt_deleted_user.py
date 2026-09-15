"""(2) JWT user-terhapus -> 401. DB/pool gagal -> 503 (bukan 401; cache tak diisi).
- auth_middleware: cek eksistensi "User" (cache 60s/5s) sesudah JWT valid; absen -> 401 USER_NOT_FOUND;
  galat DB -> 503 AUTH_DB_UNAVAILABLE. uuid tak valid -> 401 (bukan 503).
- auth.py /refresh: cek eksistensi sebelum gRPC; absen -> 401; galat DB -> 503 (poin d).
Jangkar tepat; NOL bila tak cocok."""
import io, sys

# --- (1) auth_middleware ---
M = "/root/mh-law2/backend/api_gateway/app/middleware/auth_middleware.py"
t = io.open(M, encoding="utf-8").read()

A_IMP = "from backend.api_gateway.app.services.session_manager import session_manager\n"
B_IMP = ("from backend.api_gateway.app.services.session_manager import session_manager\n"
         "from backend.api_gateway.app.services.db_pool import get_db_pool\n"
         "import time\n"
         "import uuid as _uuid\n")
if t.count(A_IMP) != 1:
    print("GAGAL import jangkar", t.count(A_IMP)); sys.exit(1)
t = t.replace(A_IMP, B_IMP)

A_LOG = "logger = logging.getLogger(__name__)\n"
HELP = '''logger = logging.getLogger(__name__)

# Cek eksistensi User (JWT valid tapi user DIHAPUS -> 401, jangan lolos sampai token kedaluwarsa).
# Cache in-proc pendek: positif 60s, negatif 5s (user dibuat-ulang tak keblok lama).
# Galat DB/pool -> ANGKAT _UserCheckError -> pemanggil balas 503 (retry), cache TIDAK diisi:
# gangguan DB sesaat TAK BOLEH me-logout semua pengguna (JWT sig tetap primer).
_USER_EXIST_TTL_POS = 60.0
_USER_EXIST_TTL_NEG = 5.0
_user_exist_cache: dict = {}


class _UserCheckError(Exception):
    """Cek eksistensi gagal karena DB/pool (transien) -> 503, bukan 401."""


async def _user_exists(user_id) -> bool:
    if not user_id:
        return False
    try:
        _uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return False  # id tak valid -> anggap tak ada -> 401 (bukan 503)
    now = time.monotonic()
    hit = _user_exist_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval('SELECT 1 FROM "User" WHERE id = $1::uuid', user_id)
    except Exception as e:
        logger.warning(f"[auth] cek eksistensi user gagal (503, cache tak diisi): {e}")
        raise _UserCheckError() from e
    exists = val is not None
    _user_exist_cache[user_id] = (
        exists,
        now + (_USER_EXIST_TTL_POS if exists else _USER_EXIST_TTL_NEG),
    )
    return exists
'''
if t.count(A_LOG) != 1:
    print("GAGAL logger jangkar", t.count(A_LOG)); sys.exit(1)
t = t.replace(A_LOG, HELP)

A_SET = '''                request.state.user = {
                    "user_id": user_id,'''
B_SET = '''                # Eksistensi User: JWT valid tapi user sudah DIHAPUS -> 401. Galat DB -> 503.
                try:
                    _user_ada = await _user_exists(user_id)
                except _UserCheckError:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "Layanan autentikasi sementara tidak tersedia",
                            "code": "AUTH_DB_UNAVAILABLE",
                        },
                    )
                if not _user_ada:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": "User tidak ditemukan",
                            "code": "USER_NOT_FOUND",
                            "force_logout": True,
                        },
                    )

                request.state.user = {
                    "user_id": user_id,'''
if t.count(A_SET) != 1:
    print("GAGAL state.user jangkar", t.count(A_SET)); sys.exit(1)
t = t.replace(A_SET, B_SET)
io.open(M, "w", encoding="utf-8").write(t)
print("OK (1) auth_middleware: eksistensi + cache + 503-on-DB-error")

# --- (2) auth.py /refresh ---
R = "/root/mh-law2/backend/api_gateway/app/routers/auth.py"
r = io.open(R, encoding="utf-8").read()
A_REF = '''        except jwt.DecodeError:
            logger.warning("Could not decode refresh token for session check")
            # Continue - let auth_service validate the token

        # Call auth service
        result = await auth_client.refresh_token(data.refresh_token)'''
B_REF = '''            # poin (d): refresh token milik user TERHAPUS tak boleh diperbarui.
            if user_id:
                _uid_valid = True
                try:
                    import uuid as _uuid_r

                    _uuid_r.UUID(str(user_id))
                except Exception:
                    _uid_valid = False
                if not _uid_valid:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="User tidak ditemukan",
                    )
                try:
                    from backend.api_gateway.app.services.db_pool import get_db_pool

                    _pool = await get_db_pool()
                    async with _pool.acquire() as _c:
                        _ex = await _c.fetchval(
                            'SELECT 1 FROM "User" WHERE id = $1::uuid', user_id
                        )
                except Exception as _dbe:
                    logger.warning(f"[refresh] cek eksistensi gagal (503): {_dbe}")
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Layanan autentikasi sementara tidak tersedia",
                    )
                if _ex is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="User tidak ditemukan",
                    )
        except jwt.DecodeError:
            logger.warning("Could not decode refresh token for session check")
            # Continue - let auth_service validate the token

        # Call auth service
        result = await auth_client.refresh_token(data.refresh_token)'''
if r.count(A_REF) != 1:
    print("GAGAL refresh jangkar", r.count(A_REF)); sys.exit(1)
r = r.replace(A_REF, B_REF)
io.open(R, "w", encoding="utf-8").write(r)
print("OK (2) auth.py /refresh: eksistensi + 503-on-DB-error")
