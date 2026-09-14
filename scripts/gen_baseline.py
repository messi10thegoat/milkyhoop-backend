"""Bangkitkan snapshot WRITE tak terpetakan & tak-exempt HARI INI (owner-only pasca tahap 3) -> /tmp/izin_write_baseline.json.
Gerbang cakupan merah bila muncul WRITE baru di luar {mapped, exempt, baseline}."""
import importlib.util
import json
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app")
from fastapi.routing import APIRoute  # noqa: E402

import app.middleware  # noqa: E402,F401
from app.main import app as fastapi_app  # noqa: E402

_PM = sys.argv[1] if len(sys.argv) > 1 else None
if _PM:
    spec = importlib.util.spec_from_file_location("app.middleware.pm_gen", _PM)
    pm = importlib.util.module_from_spec(spec); sys.modules["app.middleware.pm_gen"] = pm; spec.loader.exec_module(pm)
else:
    from app.middleware import permission_middleware as pm

TULIS = {"POST", "PUT", "PATCH", "DELETE"}
pola = [(re.compile(p), ms) for p, ms, *_ in pm.ROUTE_PERMISSIONS]
skip = [re.compile(p) for p in pm.SKIP_PATTERNS]
wex = [re.compile(p) for p, _ in pm.WRITE_EXEMPT]
contoh = lambda p: re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", p)  # noqa: E731

base = []
for r in fastapi_app.routes:
    if not isinstance(r, APIRoute):
        continue
    for m in sorted(r.methods & TULIS):
        p = contoh(r.path)
        if any(s.match(p) for s in skip) or any(x.match(p) for x in wex):
            continue
        if any(m in ms and c.match(p) for c, ms in pola):
            continue
        base.append(f"{m} {r.path}")
base = sorted(set(base))
json.dump(base, open("/tmp/izin_write_baseline.json", "w"), indent=1)
print(f"baseline (owner-only, tahap 3): {len(base)} write")
