"""Inventaris SISA write tanpa pola (tabel rute hidup) untuk tahap 3. Kelompokkan per router + usul: POLA (aturan sama)
atau PENGECUALIAN (auth/onboarding/chat-doc). Baca-saja."""
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app")
from fastapi.routing import APIRoute  # noqa: E402

from app.main import app  # noqa: E402
from app.middleware import permission_middleware as pm  # noqa: E402

TULIS = {"POST", "PUT", "PATCH", "DELETE"}
pola = [(re.compile(p), ms) for p, ms, *_ in pm.ROUTE_PERMISSIONS]
skip = [re.compile(p) for p in pm.SKIP_PATTERNS]
contoh = lambda p: re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", p)  # noqa: E731

per_router = defaultdict(list)
for r in app.routes:
    if not isinstance(r, APIRoute):
        continue
    rt = r.endpoint.__module__.split(".")[-1]
    for m in sorted(r.methods & TULIS):
        p = contoh(r.path)
        if any(s.match(p) for s in skip):
            continue
        if any(m in ms and c.match(p) for c, ms in pola):
            continue
        per_router[rt].append(f"{m} {r.path}")

# klasifikasi router: EXEMPT (auth/onboarding/chat-doc/health/public) vs perlu POLA
EXEMPT_ROUTER = {"auth", "signup", "google_auth", "session", "onboarding", "invite_public", "public_chat",
                 "setup_chat", "tenant_chat", "flow", "health", "customer", "device", "uploads",
                 "unified_chat", "chat", "chat_history", "chat_usage", "user"}
CHAT_DOC = {"unified_chat", "document_intake", "chat", "action_chat"}
tot = 0
print("=== SISA WRITE TANPA POLA (per router) ===")
for rt in sorted(per_router, key=lambda k: -len(per_router[k])):
    tag = "EXEMPT?" if rt in EXEMPT_ROUTER else "POLA"
    tot += len(per_router[rt])
    print(f"\n[{rt}] {len(per_router[rt])}  -> {tag}")
    for x in per_router[rt]:
        print("   ", x)
print(f"\nTOTAL sisa write tanpa pola: {tot} · router: {len(per_router)}")
print("\n=== usul EXEMPT vs POLA ===")
exempt = sum(len(v) for k, v in per_router.items() if k in EXEMPT_ROUTER)
print(f"router exempt-kandidat: {sorted(k for k in per_router if k in EXEMPT_ROUTER)} = {exempt} rute")
print(f"router perlu POLA: {sorted(k for k in per_router if k not in EXEMPT_ROUTER)} = {tot - exempt} rute")
