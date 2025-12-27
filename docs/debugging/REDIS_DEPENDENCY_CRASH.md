# Debugging: Redis Dependency Crash

> **Date:** 2025-12-26
> **Severity:** High
> **Affected Services:** conversation_manager, setup_orchestrator
> **Root Cause:** Redis container crashed

---

## Symptom

Button "Simpan" di Pembelian page 2 tidak merespon saat di-tap. Tidak ada feedback apapun.

**Context:** Terjadi setelah optimasi RAM (mematikan beberapa services untuk hemat memory).

---

## Investigation Steps

### 1. Check Container Status

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "milkyhoop|NAME"
```

**Finding:** 2 container dalam status `Restarting`:
- `milkyhoop-dev-conversation_manager-1`
- `milkyhoop-dev-setup_orchestrator-1`

### 2. Check Container Logs

```bash
docker logs milkyhoop-dev-conversation_manager-1 --tail 30
docker logs milkyhoop-dev-setup_orchestrator-1 --tail 30
```

**Finding:** Error yang sama di kedua container:
```
redis.exceptions.ConnectionError: Error -3 connecting to redis:6379.
Temporary failure in name resolution.
```

### 3. Check Redis Status

```bash
docker ps -a | grep redis
```

**Finding:** Redis container dalam status `Exited`:
```
milkyhoop-dev-redis   Exited (255) 4 hours ago
```

---

## Root Cause

```
Redis crashed (memory pressure dari swap 100%)
    ↓
conversation_manager & setup_orchestrator tidak bisa connect ke Redis
    ↓
Services masuk restart loop
    ↓
API calls dari frontend timeout/hang
    ↓
Button "Simpan" tidak merespon
```

---

## Solution

### Step 1: Start Redis

```bash
docker start milkyhoop-dev-redis
```

### Step 2: Restart Dependent Services

```bash
docker restart milkyhoop-dev-conversation_manager-1 milkyhoop-dev-setup_orchestrator-1
```

### Step 3: Verify All Healthy

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "Restarting|Exited"
# Should return empty (no problematic containers)
```

---

## Prevention

### 1. Monitor Redis Memory

```bash
# Check Redis memory usage
docker exec milkyhoop-dev-redis redis-cli INFO memory | grep used_memory_human
```

### 2. Set Redis Restart Policy

Ensure `docker-compose.yml` has:
```yaml
redis:
  restart: unless-stopped
```

### 3. Monitor Swap Usage

```bash
# If swap > 90%, consider:
# - Restarting non-essential services
# - Adding more RAM
# - Clearing swap (risky): sudo swapoff -a && sudo swapon -a
```

### 4. Quick Health Check Command

```bash
# Check for any crashed/restarting containers
docker ps -a --format "{{.Names}}\t{{.Status}}" | grep -E "Exited|Restarting"
```

---

## Services That Depend on Redis

| Service | Uses Redis For |
|---------|----------------|
| conversation_manager | Session management, state |
| setup_orchestrator | Session/workflow state |
| memory_service | Cache |
| context_service | Context caching |

If Redis is down, these services will fail to start.

---

## Related Issues

- [Memory optimization guide](../operations/MEMORY_OPTIMIZATION.md)
- [Docker health checks](../operations/DOCKER_HEALTH.md)
