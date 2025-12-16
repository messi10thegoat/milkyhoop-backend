# Enterprise Single Session Enforcement (WhatsApp-style)

**Tanggal Implementasi:** 17 Desember 2025, 00:19 WIB (GMT+7 Jakarta)
**Status:** ✅ COMPLETED & TESTED

---

## Executive Summary

Implementasi sistem single session enforcement bergaya WhatsApp untuk aplikasi MilkyHoop. Sistem ini memastikan bahwa setiap user hanya dapat memiliki satu sesi aktif per tipe device (mobile/web), dengan mekanisme automatic session replacement ketika login dari device baru.

### Key Principle
```
JWT ≠ Session
Session Authority = SERVER STATE (Redis)
```

---

## Arsitektur Sistem

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ENTERPRISE SESSION ENFORCEMENT FLOW                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐         ┌───────────────┐         ┌──────────────────┐       │
│  │  Client  │ ──1──▶  │  API Gateway  │ ──2──▶  │   Auth Service   │       │
│  │ (Mobile) │         │  (Port 9000)  │         │   (gRPC:8013)    │       │
│  └──────────┘         └───────────────┘         └──────────────────┘       │
│       │                      │                          │                   │
│       │                      │                          │                   │
│       │                      ▼                          ▼                   │
│       │               ┌─────────────┐           ┌──────────────┐           │
│       │               │   Redis     │           │  PostgreSQL  │           │
│       │               │  (Session   │           │   (Users,    │           │
│       │               │  Authority) │           │   Tokens)    │           │
│       │               └─────────────┘           └──────────────┘           │
│       │                      │                                              │
│       │◀─────────────────────┘                                              │
│       │         Session validation on every request                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Login Flow (Mobile)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           LOGIN FLOW (Mobile)                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. Client POST /api/auth/login                                             │
│          │                                                                  │
│          ▼                                                                  │
│  2. API Gateway generates device_id (UUID)                                  │
│          │                                                                  │
│          ▼                                                                  │
│  3. gRPC call to Auth Service with device_id in metadata                    │
│          │                                                                  │
│          ▼                                                                  │
│  4. Auth Service creates JWT with embedded device_id + device_type          │
│          │                                                                  │
│          ▼                                                                  │
│  5. API Gateway calls SessionManager.activate_mobile_device()               │
│          │                                                                  │
│          ▼                                                                  │
│  6. Redis ATOMIC operation:                                                 │
│     ┌────────────────────────────────────────────────────┐                 │
│     │  PIPELINE (transaction=True):                       │                 │
│     │    SET session:{user_id}:mobile = {device_id}       │                 │
│     │    DEL session:{user_id}:web  (cascade kill)        │                 │
│     └────────────────────────────────────────────────────┘                 │
│          │                                                                  │
│          ▼                                                                  │
│  7. Return JWT + device_id to client                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Request Validation Flow (KILL SWITCH)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      REQUEST VALIDATION FLOW                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. Client sends request with Bearer token                                  │
│          │                                                                  │
│          ▼                                                                  │
│  2. Auth Middleware validates JWT (signature, expiry)                       │
│          │                                                                  │
│          ▼                                                                  │
│  3. Extract device_id & device_type from JWT payload                        │
│          │                                                                  │
│          ▼                                                                  │
│  4. SessionManager.is_session_valid(user_id, device_type, device_id)        │
│          │                                                                  │
│          ├──▶ Redis GET session:{user_id}:{device_type}                     │
│          │                                                                  │
│          ▼                                                                  │
│  5. Compare JWT device_id with Redis device_id                              │
│          │                                                                  │
│          ├──▶ MATCH: Allow request ✅                                       │
│          │                                                                  │
│          └──▶ MISMATCH: Return 401 SESSION_REPLACED ❌                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Redis Session Authority Schema

### Key Structure
```
session:{user_id}:{device_type} = {device_id}
```

### Example
```redis
# User dengan ID d780b7fe-8b53-47e4-8ef1-aad067de0d58
session:d780b7fe-8b53-47e4-8ef1-aad067de0d58:mobile = "95b30bc1-c9c2-4d17-8189-2bf51f0d38e1"
session:d780b7fe-8b53-47e4-8ef1-aad067de0d58:web    = "abc12345-..."

# TTL: 8 days (refresh_token_expiry + buffer)
TTL = 691200 seconds
```

### Business Rules

| Event | Action | Result |
|-------|--------|--------|
| Mobile Login | `SET mobile` + `DEL web` (atomic) | Old mobile & web sessions killed |
| Web Login (QR) | `SET web` only | Mobile session unaffected |
| Mobile Logout | `DEL mobile` + `DEL web` | All sessions killed |
| Web Logout | `DEL web` only | Mobile session unaffected |

---

## JWT Token Structure

### Access Token Payload
```json
{
  "user_id": "d780b7fe-8b53-47e4-8ef1-aad067de0d58",
  "tenant_id": "evlogia",
  "role": "FREE",
  "email": "grapmanado@gmail.com",
  "username": "evlogia",
  "device_id": "95b30bc1-c9c2-4d17-8189-2bf51f0d38e1",
  "device_type": "mobile",
  "token_type": "access",
  "iat": 1765905331,
  "exp": 1766510131,
  "nbf": 1765905331
}
```

### Device Claims Purpose
- **device_id**: Unique identifier untuk session instance (UUID)
- **device_type**: `"mobile"` atau `"web"` untuk segregasi session

---

## Files yang Dimodifikasi

### 1. API Gateway - Auth Router
**File:** `backend/api_gateway/app/routers/auth.py`

```python
# Generate device_id BEFORE calling auth_service
device_id = str(uuid.uuid4())
device_type = "mobile"

result = await auth_client.login_user(
    email=request.email,
    password=request.password,
    device_id=device_id,
    device_type=device_type
)

# Atomic session activation
session_manager.activate_mobile_device(
    user_id=result["user_id"],
    device_id=device_id
)
```

### 2. API Gateway - Auth Client (gRPC)
**File:** `backend/api_gateway/app/services/auth_client.py`

```python
# Pass device claims via gRPC metadata
async def login_user(
    self,
    email: str,
    password: str,
    device_id: str = None,
    device_type: str = None
) -> Dict[str, Any]:
    metadata = {}
    if device_id:
        metadata["device_id"] = device_id
    if device_type:
        metadata["device_type"] = device_type

    request = auth_pb2.LoginRequest(
        email=email,
        password=password,
        metadata=metadata
    )
```

### 3. Auth Service - gRPC Server
**File:** `backend/services/auth_service/app/grpc_server.py`

```python
# Extract device claims and embed in JWT
device_id = request.metadata.get("device_id") if request.metadata else None
device_type = request.metadata.get("device_type") if request.metadata else None

access_token = self.jwt_handler.create_access_token(
    user_id=user.id,
    tenant_id=user.tenantId,
    role=user.role,
    email=user.email,
    username=user.username,
    device_id=device_id,
    device_type=device_type
)
```

### 4. Auth Service - JWT Handler
**File:** `backend/services/auth_service/app/utils/jwt_handler.py`

```python
@staticmethod
def create_access_token(
    user_id: str,
    tenant_id: str,
    role: str,
    email: str,
    username: str,
    device_id: str = None,
    device_type: str = None
) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email": email,
        "username": username,
        "device_id": device_id,
        "device_type": device_type,
        "token_type": "access",
        "iat": now,
        "exp": expires_at,
        "nbf": now
    }
```

### 5. API Gateway - Auth Middleware (KILL SWITCH)
**File:** `backend/api_gateway/app/middleware/auth_middleware.py`

```python
# Session authority check - FAIL-CLOSED
if device_id and device_type:
    if not session_manager.is_session_valid(user_id, device_type, device_id):
        logger.warning(
            f"Session replaced for user {user_id[:8]}..., device_type={device_type}"
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": "Session telah digantikan di perangkat lain",
                "code": "SESSION_REPLACED",
                "force_logout": True
            },
        )
```

### 6. API Gateway - Session Manager
**File:** `backend/api_gateway/app/services/session_manager.py`

```python
def activate_mobile_device(self, user_id: str, device_id: str) -> bool:
    """
    ATOMIC: Set mobile session + revoke web session in single transaction.
    No race condition window.
    """
    pipe = self.redis.pipeline(transaction=True)
    pipe.set(self._key(user_id, "mobile"), device_id, ex=self.TTL_SECONDS)
    pipe.delete(self._key(user_id, "web"))  # Cascade kill
    pipe.execute()
    return True

def activate_web_device(self, user_id: str, device_id: str) -> bool:
    """Set web session (does NOT affect mobile session)."""
    return self.set_active_device(user_id, "web", device_id)
```

---

## Security Features

| Feature | Status | Description |
|---------|--------|-------------|
| JWT Device Claims | ✅ | device_id + device_type embedded in JWT |
| Redis Session Authority | ✅ | Single source of truth for active sessions |
| Atomic Session Replacement | ✅ | Redis pipeline untuk no race condition |
| Refresh Token Lock | ✅ | Can't refresh setelah session replaced |
| Cascade Web Kill | ✅ | Mobile login kills web session |
| KILL SWITCH Middleware | ✅ | Session check on every authenticated request |
| FAIL-CLOSED | ✅ | Missing device claims = invalid session |

---

## Error Codes

| Code | HTTP Status | Meaning | Client Action |
|------|-------------|---------|---------------|
| `SESSION_REPLACED` | 401 | Session digantikan oleh login dari device lain | Force logout, redirect to login |
| `INVALID_TOKEN` | 401 | JWT invalid atau expired | Redirect to login |
| `MISSING_TOKEN` | 401 | Authorization header tidak ada | Redirect to login |

### Client Response Example
```json
{
  "error": "Session telah digantikan di perangkat lain",
  "code": "SESSION_REPLACED",
  "force_logout": true
}
```

---

## Configuration

### Environment Variables
```bash
# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<redis_password>

# Database
DATABASE_URL=postgresql://user:pass@host:port/db

# JWT
JWT_SECRET=<secret>
ACCESS_TOKEN_EXPIRE_MINUTES=10080  # 7 days
REFRESH_TOKEN_EXPIRE_DAYS=30
```

### Session TTL
```python
# SessionManager
TTL_SECONDS = 8 * 24 * 60 * 60  # 8 days (refresh_token_expiry + buffer)
```

---

## Test Results

### Test 1: Session Replacement
```
=== TEST 1: Session Replacement ===
Login pertama (device A)...
✅ Login 1 berhasil - Device A: 392239ec-0d2a-4b...

Login kedua (device B - should replace A)...
✅ Login 2 berhasil - Device B: e6720249-c84b-4b...

Test: Request dengan Token A (should get 401 SESSION_REPLACED)...
HTTP Code: 401
Body: {"error":"Session telah digantikan di perangkat lain","code":"SESSION_REPLACED","force_logout":true}
✅ TEST 1 PASSED: Token A rejected with SESSION_REPLACED
```

### Test 2: Refresh Token Lock
```
=== TEST 2: Refresh Token Lock ===
Login ketiga (device C - should invalidate B)...
✅ Login 3 berhasil

Test: Refresh dengan Token B (should fail - session replaced)...
HTTP Code: 401
Body: {"detail":"Token refresh failed. Please login again."}
✅ TEST 2 PASSED: Refresh token B rejected after session replaced
```

---

## Deployment Notes

### Container Dependencies
```
api_gateway ──depends_on──▶ redis
api_gateway ──depends_on──▶ postgres
auth_service ──depends_on──▶ postgres
```

### Service Discovery

| Service | Hostname | Port | Protocol |
|---------|----------|------|----------|
| PostgreSQL | `postgres` | 5432 | TCP |
| Redis | `redis` | 6379 | TCP |
| Auth Service | `milkyhoop-dev-auth_service-1` | 8013 | gRPC |
| API Gateway | `milkyhoop-dev-api_gateway` | 8000 | HTTP |

### Network Alias
PostgreSQL container harus memiliki network alias `postgres` agar services dapat resolve hostname:
```bash
docker run --network-alias postgres --network milkyhoop_dev_network ...
```

---

## Network Architecture

### Unified Network Design
Semua services berjalan dalam single Docker network (`milkyhoop_dev_network`) untuk simplicity dan reliability.

```
┌─────────────────────────────────────────────────────────────────┐
│                    milkyhoop_dev_network                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   nginx:443  │  │  api_gateway │  │ auth_service │          │
│  │   frontend   │──│   :8000      │──│   :8013      │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                 │                   │
│         └─────────────────┼─────────────────┘                   │
│                           │                                     │
│              ┌────────────┴────────────┐                        │
│              │                         │                        │
│        ┌─────────────┐          ┌─────────────┐                │
│        │   Redis     │          │  PostgreSQL │                │
│        │   :6379     │          │   :5432     │                │
│        └─────────────┘          └─────────────┘                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Network Configuration (docker-compose-dev.yml)
```yaml
networks:
  milkyhoop_dev_network:
    external: true
    name: milkyhoop_dev_network
```

**Best Practice:**
- Single unified network untuk semua services
- External network untuk persistence across restarts
- Network alias untuk service discovery

---

## Infrastructure Configuration

### PostgreSQL Setup
```yaml
postgres:
  image: postgres:14  # Must match production data version
  container_name: milkyhoop-dev-postgres
  environment:
    POSTGRES_USER: postgres
    POSTGRES_PASSWORD: <password>
    POSTGRES_DB: milkydb
  ports:
    - "6432:5432"  # External:Internal
  volumes:
    - milkyhoop-dev_postgres_data:/var/lib/postgresql/data
  networks:
    - milkyhoop_dev_network

volumes:
  milkyhoop-dev_postgres_data:
    external: true  # Use existing production volume with data
```

### Redis Setup
```yaml
redis:
  image: redis:7
  container_name: milkyhoop-dev-redis
  command: redis-server --requirepass <password>
  ports:
    - "7379:6379"  # External:Internal
  networks:
    - milkyhoop_dev_network
```

### Critical Notes
- **PostgreSQL Version**: Harus `postgres:14` untuk kompatibilitas dengan production data
- **Volume External**: Set `external: true` untuk menggunakan volume dengan data existing
- **Redis Password**: Wajib untuk security, diakses via `REDIS_PASSWORD` env var

---

## Changelog

| Tanggal | Perubahan |
|---------|-----------|
| 17 Des 2025 00:19 WIB | Initial implementation completed |
| 17 Des 2025 00:19 WIB | All tests passed (Session Replacement + Refresh Token Lock) |
| 16 Des 2025 | Network architecture unified to single network (`milkyhoop_dev_network`) |
| 16 Des 2025 | PostgreSQL volume fixed for chat history persistence |
| 16 Des 2025 | Documentation optimized with infrastructure details |

---

**Author:** Claude Code
**Last Updated:** 16 Desember 2025, 18:00 WIB (GMT+7 Jakarta)
