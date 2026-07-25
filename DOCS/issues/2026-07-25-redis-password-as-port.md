# Issue: gateway parses REDIS_PASSWORD into the port slot -> redis unusable

## Symptom
api_gateway log: "Redis connection failed: Port could not be cast to integer value as
'x66dii8PjJ7ADL094'". /ready -> 503 {"db":true,"redis":false}. Present since >= 2026-07-24
09:11 (predates the DP deploy; restart-only deploy did not change it).

## Cause (to confirm at fix time)
REDIS_PASSWORD (x66dii8...) is ending up where the port is expected — a malformed REDIS_URL
assembly or a redis client constructed with positional args in the wrong order. Env has
REDIS_PASSWORD set; the URL/port derivation is wrong.

## Impact
App runs on in-memory fallbacks: rate limiter per-worker ("not suitable for multi-instance"),
dashboard cache per-worker. NOT on the steps 0-9 ledger path (idempotency=DB Law-14;
policy_engine=in-process from DB; auth session-authority DISABLED `if False`). So correctness of
the DP flow is unaffected; this is degraded caching/rate-limiting only.

## HAZARD at fix time
Do NOT `docker compose up redis` / recompose redis. Memory `redis-misconf`: running redis is
redis:latest with password x66dii8..., compose is STALE (redis:7 + different pass) -> recompose
breaks gateway auth. Fix the URL/port parse in the gateway config, or align env, WITHOUT
recomposing the redis container.
