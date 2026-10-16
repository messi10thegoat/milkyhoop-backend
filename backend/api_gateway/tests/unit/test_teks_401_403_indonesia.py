"""TEKS-401-403-ID (16 Okt 2026): detail HTTPException 401/403 di seluruh app + 429/WAF/lockout berbahasa Indonesia.
Status, kunci, code TIDAK berubah. Keluarga 'Cannot post ... period' diterjemahkan terpisah (test_teks_periode_id.py, setelah FE r285 dua-bahasa)."""
import ast
import re
from pathlib import Path

from app.middleware import account_lockout_middleware as AL
from app.middleware import rate_limit_middleware as RL
from app.middleware import waf_middleware as WF

APP = Path(AL.__file__).resolve().parents[1]
LARANG = re.compile(
    r"Authentication required|Invalid user context|User ID required|Not authenticated|No tenant context|Unauthorized|"
    r"Authentication failed|Session not found|No active session|Token refresh failed|Tenant not found|Invalid user session|"
    r"Invalid token|Permission denied|Access denied|Permission check failed|Unable to verify|Insufficient permissions|"
    r"Only Owner|Only OWNER|Only requester|Not authorized|No pay group|No visible employees|Admin access required|"
    r"No access to this tenant|Cannot (modify|reopen|remove|delete)|Period reopening|You don't have", re.I)
KELUARGA_PERIODE = re.compile(r"Cannot (post|create adjustment)")


def _detail_401_403():
    for p in APP.rglob("*.py"):
        if "/libs/" in str(p):
            continue
        try:
            t = ast.parse(p.read_text())
        except Exception:
            continue
        for n in ast.walk(t):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "HTTPException":
                sc = dt = None
                for k in n.keywords:
                    if k.arg == "status_code" and isinstance(k.value, ast.Constant):
                        sc = k.value.value
                    if k.arg == "detail":
                        dt = k.value
                if n.args and isinstance(n.args[0], ast.Constant):
                    sc = n.args[0].value
                if sc in (401, 403) and dt is not None:
                    yield p.relative_to(APP), n.lineno, ast.unparse(dt)


def test_detail_401_403_tak_berbahasa_inggris():
    jelek = [(str(p), ln, d[:80]) for p, ln, d in _detail_401_403() if LARANG.search(d) and not KELUARGA_PERIODE.search(d)]
    assert not jelek, jelek[:10]


def test_429_lockout_waf_indonesia_kode_tetap():
    for m, kode in ((RL, "RATE_LIMIT_EXCEEDED"), (AL, "ACCOUNT_LOCKED"), (WF, "IP_BLOCKED"), (WF, "WAF_BLOCKED")):
        s = Path(m.__file__).read_text()
        assert kode in s
        for t in ("Too many requests", "Too many failed attempts", "Please retry after", "Account temporarily locked",
                  "Request blocked by WAF", '"IP blocked"'):  # log internal (logger) tak dihitung
            assert t not in s, (m.__name__, t)
    assert "status_code=429" in Path(RL.__file__).read_text()
    assert "Retry-After" in Path(RL.__file__).read_text() and "Retry-After" in Path(AL.__file__).read_text()
