# Smart Login Architecture

> WhatsApp-style Single Session Enforcement untuk MilkyHoop

## Overview

Smart Login adalah sistem autentikasi yang menerapkan **single session enforcement** seperti WhatsApp Web. Fitur utama:

- **1 User = 1 Web Session TOTAL** (bukan per browser)
- Login baru otomatis me-logout SEMUA sesi web yang ada
- QR-based login untuk desktop browser
- Real-time force logout via WebSocket
- Multi-tab support dengan koordinasi force logout

```
┌─────────────────────────────────────────────────────────────────┐
│                    SMART LOGIN SYSTEM                           │
├─────────────────────────────────────────────────────────────────┤
│  Mobile App (Primary)  ←──────→  Web Browser (Secondary)        │
│  - Email/Password Login          - QR-only Login                │
│  - Scan QR to approve            - Receives force logout        │
│  - Control all web sessions      - 1 active session per user    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Architecture Diagram

### QR Login Flow

```
┌──────────────────┐                                  ┌──────────────────┐
│   Desktop Web    │                                  │    Mobile App    │
│    Browser       │                                  │   (Logged In)    │
└────────┬─────────┘                                  └────────┬─────────┘
         │                                                     │
         │ 1. POST /api/auth/qr/generate                       │
         │    {browser_id, fingerprint}                        │
         │─────────────────────────────►                       │
         │                              ┌──────────────────┐   │
         │                              │   API Gateway    │   │
         │◄─────────────────────────────│                  │   │
         │ 2. Returns {token, qr_url}   └──────────────────┘   │
         │                                                     │
         │ 3. Connect WebSocket                                │
         │    /api/auth/qr/ws/{token}                          │
         │─────────────────────────────►                       │
         │                                                     │
         │ 4. Display QR Code                                  │
         │    ┌─────────┐                                      │
         │    │ ▓▓▓▓▓▓▓ │                                      │
         │    │ ▓     ▓ │◄───────────────────────────┐         │
         │    │ ▓▓▓▓▓▓▓ │                            │         │
         │    └─────────┘                            │         │
         │                           5. Scan QR Code │         │
         │                                           │         │
         │                              6. POST /api/auth/qr/scan
         │                                 {token}   │─────────│
         │◄────────────────────────────────────────────────────│
         │ 7. WS Event: "scanned"                              │
         │                                                     │
         │                              8. POST /api/auth/qr/approve
         │                                 {token, approved: true}
         │◄────────────────────────────────────────────────────│
         │ 9. WS Event: "approved"                             │
         │    {access_token, refresh_token, device_id, user}   │
         │                                                     │
         │ 10. Store tokens & connect device WebSocket         │
         │     /api/devices/ws/{device_id}?tab_id={tab_id}     │
         │─────────────────────────────►                       │
         ▼                                                     ▼
```

### Single Session Enforcement Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                     SESSION ENFORCEMENT FLOW                          │
└──────────────────────────────────────────────────────────────────────┘

User already has active session on Browser A:

┌─────────────┐                           ┌─────────────┐
│ Browser A   │    Active Session         │   Server    │
│ (Tab 1)     │◄─────────────────────────►│             │
│ (Tab 2)     │    device_id_A            │             │
└─────────────┘                           └─────────────┘

User logs in on Browser B:

┌─────────────┐     1. QR Login           ┌─────────────┐
│ Browser B   │──────────────────────────►│   Server    │
└─────────────┘                           └──────┬──────┘
                                                 │
                  2. Find ALL active             │
                     web sessions ──────────────►│
                                                 │
                  3. WebSocket: force_logout     │
┌─────────────┐◄─────────────────────────────────│
│ Browser A   │     to ALL tabs (Tab 1 & 2)      │
│ (Tab 1) ❌  │                                  │
│ (Tab 2) ❌  │     4. Deactivate device_id_A    │
└─────────────┘                                  │
                                                 │
                  5. Create device_id_B          │
┌─────────────┐◄─────────────────────────────────│
│ Browser B   │     Active Session              │
│ ✅          │     device_id_B                  │
└─────────────┘                           └─────────────┘
```

---

## Backend Components

### 1. Device Service
**Path:** `backend/api_gateway/app/services/device_service.py`

Core service untuk session enforcement dan device management.

```python
# Session Enforcement Constants
WEB_SESSION_TTL_DAYS = 30   # Web session expires after 30 days
GRACE_SECONDS = 0.2          # Grace period for force logout
MAX_RETRIES = 2              # Race condition retry handling
```

**Key Methods:**

| Method | Deskripsi |
|--------|-----------|
| `register_device()` | Register device baru + kick sesi lama |
| `list_devices()` | List semua device aktif user |
| `logout_device()` | Logout device spesifik |
| `logout_all_web_devices()` | Cascade logout semua web |
| `update_device_activity()` | Update last activity timestamp |
| `cleanup_expired_devices()` | Cleanup expired sessions (cron) |

**Register Device Flow:**
```python
async def register_device(self, user_id, tenant_id, device_type, browser_id, ...):
    if device_type == "web":
        # 1. Find ALL active web sessions (NO browser_id filter!)
        existing_sessions = await prisma.userdevice.find_many(
            where={"userId": user_id, "deviceType": "web", "isActive": True}
        )

        # 2. Notify via WebSocket FIRST (side-effect OUTSIDE transaction)
        for existing in existing_sessions:
            await websocket_hub.force_logout_device(existing.id, "Session digantikan")

        # 3. Grace period for clients to react
        await asyncio.sleep(GRACE_SECONDS)

        # 4. Deactivate all existing sessions
        for existing in existing_sessions:
            await prisma.userdevice.update(...)

        # 5. Create new device
        device = await prisma.userdevice.create(...)
        return device.id
```

### 2. QR Token Service
**Path:** `backend/api_gateway/app/services/qr_token_service.py`

Mengelola lifecycle QR token untuk login.

```python
# QR Token Configuration
QR_TOKEN_TTL_SECONDS = 120  # QR tokens expire after 2 minutes
```

**Token States:**
```
pending → scanned → approved/rejected
   │
   └──────→ expired (after 2 minutes)
```

**Key Methods:**

| Method | Deskripsi |
|--------|-----------|
| `generate_token()` | Generate QR token baru untuk desktop |
| `check_status()` | Check status token (polling) |
| `scan_token()` | Mobile scan QR code |
| `approve_login()` | Mobile approve/reject login |
| `cleanup_expired()` | Delete expired tokens (cron) |

### 3. WebSocket Hub
**Path:** `backend/api_gateway/app/services/websocket_hub.py`

Singleton manager untuk WebSocket connections.

```python
class WebSocketHub:
    def __init__(self):
        # QR token -> WebSocket (desktop waiting)
        self.qr_connections: Dict[str, WebSocket] = {}

        # Device ID -> Tab ID -> WebSocket (multi-tab support)
        self.device_connections: Dict[str, Dict[str, WebSocket]] = {}

        # Lock for thread safety
        self._lock = asyncio.Lock()
```

**Multi-Tab Architecture:**
```
device_connections = {
    "device_A": {
        "tab_1": WebSocket,
        "tab_2": WebSocket,
        "tab_3": WebSocket
    },
    "device_B": {
        "tab_1": WebSocket
    }
}
```

**Key Methods:**

| Method | Deskripsi |
|--------|-----------|
| `register_qr()` | Register WebSocket untuk QR status |
| `send_to_qr()` | Send event ke desktop browser |
| `register_device()` | Register device WebSocket (multi-tab) |
| `force_logout_device()` | Force logout ke SEMUA tabs device |
| `cleanup_stale_connections()` | Cleanup dead connections |

### 4. QR Auth Router
**Path:** `backend/api_gateway/app/routers/qr_auth.py`

REST endpoints untuk QR authentication.

| Endpoint | Auth | Deskripsi |
|----------|------|-----------|
| `POST /api/auth/qr/generate` | No | Generate QR token |
| `GET /api/auth/qr/status/{token}` | No | Polling status |
| `WS /api/auth/qr/ws/{token}` | No | WebSocket real-time |
| `POST /api/auth/qr/scan` | Yes | Mobile scan QR |
| `POST /api/auth/qr/approve` | Yes | Mobile approve/reject |

### 5. Device Router
**Path:** `backend/api_gateway/app/routers/device.py`

REST endpoints untuk device management.

| Endpoint | Auth | Deskripsi |
|----------|------|-----------|
| `GET /api/devices` | Yes | List linked devices |
| `DELETE /api/devices/{id}` | Yes | Logout specific device |
| `POST /api/devices/logout-all-web` | Yes | Logout all web sessions |
| `WS /api/devices/ws/{device_id}` | No | Force logout WebSocket |
| `GET /api/devices/stats` | Admin | Connection statistics |

---

## Frontend Components

### 1. Device Utilities
**Path:** `frontend/web/src/utils/device.ts`

Utilities untuk device detection dan ID management.

```typescript
// Browser ID - stored in localStorage (shared across tabs)
export const getBrowserId = (): string => {
  const BROWSER_ID_KEY = 'milkyhoop_browser_id';
  let browserId = localStorage.getItem(BROWSER_ID_KEY);

  if (!browserId) {
    browserId = crypto.randomUUID();
    localStorage.setItem(BROWSER_ID_KEY, browserId);
  }
  return browserId;
};

// Tab ID - stored in sessionStorage (unique per tab)
export const getOrCreateTabId = (): string => {
  const TAB_ID_KEY = 'milkyhoop_tab_id';
  let tabId = sessionStorage.getItem(TAB_ID_KEY);

  if (!tabId) {
    tabId = `tab_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    sessionStorage.setItem(TAB_ID_KEY, tabId);
  }
  return tabId;
};
```

**Storage Strategy:**

| Storage | Key | Scope | Persist |
|---------|-----|-------|---------|
| `localStorage` | `browser_id` | All tabs same browser | Yes |
| `sessionStorage` | `tab_id` | Single tab only | Until tab close |

### 2. WebSocket Clients
**Path:** `frontend/web/src/utils/websocket.ts`

WebSocket clients untuk QR login dan force logout.

**QRWebSocketClient:**
```typescript
export class QRWebSocketClient {
  private ws: WebSocket | null = null;
  private maxReconnectAttempts = 3;

  connect(): void {
    const wsUrl = `wss://${host}/api/auth/qr/ws/${this.token}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      // Handle: connected, scanned, approved, rejected, expired
      this.onEvent(data);
    };
  }
}
```

**DeviceWebSocketClient:**
```typescript
export class DeviceWebSocketClient {
  private ws: WebSocket | null = null;
  private maxReconnectAttempts = 5;

  connect(): void {
    const wsUrl = `wss://${host}/api/devices/ws/${this.deviceId}?tab_id=${this.tabId}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.event === 'force_logout') {
        // Clear tokens, redirect to login
        this.handleForceLogout(data.reason);
      }
    };
  }
}
```

---

## WebSocket Events

### QR Events (Desktop → Server ← Mobile)

| Event | Direction | Payload | Trigger |
|-------|-----------|---------|---------|
| `connected` | Server → Desktop | `{message}` | WebSocket connected |
| `scanned` | Server → Desktop | `{message}` | Mobile scans QR |
| `approved` | Server → Desktop | `{access_token, refresh_token, device_id, user}` | Mobile approves |
| `rejected` | Server → Desktop | `{message}` | Mobile rejects |
| `expired` | Server → Desktop | `{message}` | Token expires |
| `ping/pong` | Bidirectional | `{}` | Keepalive (30s) |

### Device Events (Server → Browser)

| Event | Direction | Payload | Trigger |
|-------|-----------|---------|---------|
| `connected` | Server → Browser | `{device_id, tab_id}` | WebSocket connected |
| `force_logout` | Server → Browser | `{reason}` | Session invalidated |
| `ping/pong` | Bidirectional | `{}` | Keepalive (30s) |

---

## Session Enforcement Rules

### Core Rules

1. **1 User = 1 Web Session TOTAL**
   - Tidak per browser, tapi TOTAL untuk user
   - Login baru = kick SEMUA sesi web existing

2. **WebSocket Notification FIRST**
   - Kirim force_logout via WebSocket sebelum DB update
   - Grace period 0.2s untuk client react
   - Ini mencegah race condition

3. **browser_id untuk Identification, bukan Enforcement**
   - browser_id hanya untuk logging/identification
   - TIDAK digunakan untuk filter saat kick session

4. **Mobile sebagai Primary Device**
   - Mobile device `isPrimary: true`
   - Mobile TIDAK expire
   - Mobile control semua web sessions

### Enforcement Flow

```
User Login Baru:
    │
    ▼
1. Find ALL active web sessions (tanpa filter browser_id)
    │
    ▼
2. WebSocket: force_logout ke SEMUA tabs
    │
    ▼
3. Sleep 0.2s (grace period)
    │
    ▼
4. DB: Deactivate semua existing sessions
    │
    ▼
5. DB: Create new device entry
    │
    ▼
6. Return device_id ke client
```

---

## Configuration

### Backend Constants

```python
# device_service.py
WEB_SESSION_TTL_DAYS = 30     # Web session TTL
GRACE_SECONDS = 0.2            # Force logout grace period
MAX_RETRIES = 2                # DB transaction retry

# qr_token_service.py
QR_TOKEN_TTL_SECONDS = 120     # QR token TTL (2 minutes)
```

### Frontend Constants

```typescript
// QRWebSocketClient
maxReconnectAttempts = 3;
reconnectDelay = 2000;         // 2 seconds
pingInterval = 30000;          // 30 seconds

// DeviceWebSocketClient
maxReconnectAttempts = 5;
reconnectDelay = 3000 * attempt; // Exponential backoff
pingInterval = 30000;          // 30 seconds
```

---

## Security Features

### Token Security

| Feature | Implementation |
|---------|----------------|
| QR Token Generation | `secrets.token_urlsafe(24)` (~32 chars) |
| Refresh Token Hashing | SHA256 |
| QR Token TTL | 2 minutes (very short-lived) |
| Web Session TTL | 30 days of inactivity |

### Device Tracking

```python
# Data yang di-track per device
{
    "userId": str,
    "tenantId": str,
    "deviceType": "web" | "mobile",
    "browserId": str,           # Browser profile identifier
    "deviceName": str,          # "Chrome - Windows"
    "deviceFingerprint": str,   # Browser fingerprint
    "userAgent": str,           # Full user agent
    "lastIp": str,              # Last IP address
    "refreshTokenHash": str,    # Hashed refresh token
    "isActive": bool,
    "isPrimary": bool,
    "lastActiveAt": datetime,
    "expiresAt": datetime
}
```

### Race Condition Handling

```python
# Retry logic untuk DB transactions
attempt = 0
while attempt < MAX_RETRIES:
    try:
        # Deactivate existing + create new
        ...
        break
    except Exception:
        attempt += 1
        await asyncio.sleep(0.1 * attempt)
        # Re-fetch existing sessions for retry
        existing_sessions = await prisma.userdevice.find_many(...)
```

---

## Database Schema

### UserDevice Table

```sql
CREATE TABLE "UserDevice" (
    "id" TEXT PRIMARY KEY,
    "userId" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "deviceType" TEXT NOT NULL,       -- 'web' | 'mobile'
    "browserId" TEXT,                 -- Browser profile ID
    "deviceName" TEXT,                -- Human-readable name
    "deviceFingerprint" TEXT,
    "userAgent" TEXT,
    "lastIp" TEXT,
    "refreshTokenHash" TEXT,
    "isActive" BOOLEAN DEFAULT true,
    "isPrimary" BOOLEAN DEFAULT false,
    "lastActiveAt" TIMESTAMP DEFAULT NOW(),
    "expiresAt" TIMESTAMP,
    "createdAt" TIMESTAMP DEFAULT NOW(),

    FOREIGN KEY ("userId") REFERENCES "User"("id"),
    FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id")
);
```

### QrLoginToken Table

```sql
CREATE TABLE "QrLoginToken" (
    "id" TEXT PRIMARY KEY,
    "token" TEXT UNIQUE NOT NULL,
    "status" TEXT DEFAULT 'pending',   -- pending, scanned, approved, rejected
    "webFingerprint" TEXT,
    "webUserAgent" TEXT,
    "webIp" TEXT,
    "browserId" TEXT,
    "approvedByUserId" TEXT,
    "approvedByTenantId" TEXT,
    "approvedAt" TIMESTAMP,
    "expiresAt" TIMESTAMP NOT NULL,
    "createdAt" TIMESTAMP DEFAULT NOW(),

    FOREIGN KEY ("approvedByUserId") REFERENCES "User"("id"),
    FOREIGN KEY ("approvedByTenantId") REFERENCES "Tenant"("id")
);
```

---

## Monitoring & Debugging

### WebSocket Stats Endpoint

```bash
GET /api/devices/stats
Authorization: Bearer <admin_token>

Response:
{
    "success": true,
    "websocket_stats": {
        "qr_connections": 5,        # Active QR login attempts
        "device_connections": 12,   # Unique devices connected
        "total_tabs": 28            # Total WebSocket connections
    }
}
```

### Log Patterns

```python
# Device registration
"✅ Device registered: {device_id}... (web) browser={browser_id}... for user {user_id}..."

# Force logout
"🔴 Force logout broadcasting to {count} tabs for device {device_id}..."
"🔴 Force logout SENT to device {device_id}... tab={tab_id}..."

# WebSocket events
"🔌 Device WebSocket {device_id}... tab={tab_id}... DISCONNECT: code={code}"
"❌ Device WebSocket unregistered: {device_id}... tab={tab_id}..."
```

---

## Related Files

| Category | Files |
|----------|-------|
| **Backend Services** | `device_service.py`, `qr_token_service.py`, `websocket_hub.py` |
| **Backend Routers** | `qr_auth.py`, `device.py` |
| **Frontend Utils** | `device.ts`, `websocket.ts` |
| **Database** | `prisma/schema.prisma` |

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2025-12-15 | 1.0 | Initial implementation - Smart Login with Single Session Enforcement |

---

*Generated with Claude Code*
