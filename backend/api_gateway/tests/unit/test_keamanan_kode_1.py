"""TIKET keamanan kode gelombang 1 (audit WORKSPACE 28 Nov, B3/B4/B8): saringan tenant + ReDoS WAF."""
import re
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def _src(f):
    return (APP / f).read_text()


def test_b3_slip_gaji_disaring_tenant():
    s = _src("routers/payroll_runs.py")
    i = s.index('@router.get("/{run_id}/slips")')
    blok = s[i:i + 2500]
    assert "FROM payroll_runs WHERE id = $1 AND tenant_id = $2" in blok and blok.count("psl.tenant_id = $") == 2 and "SET LOCAL app.tenant_id = '{" not in blok


def test_b3_barcode_dalam_tenant():
    assert "WHERE barcode = $1 AND id != $2 AND tenant_id = $3" in _src("routers/products.py")


def test_b3_logo_hanya_milik_tenant(tmp_path, monkeypatch):
    from app.routers import tenant_profile as TP
    monkeypatch.setattr(TP, "LOGO_DIR", tmp_path)
    for n in ("abc.png", "abc-1700000000.png", "abc-def-1700000000.png", "abc-def.png", "abcd.png"):
        (tmp_path / n).write_bytes(b"x")
    assert sorted(Path(p).name for p in TP._logo_milik("abc")) == ["abc-1700000000.png", "abc.png"]
    assert "glob.glob(str(LOGO_DIR" not in _src("routers/tenant_profile.py")


def test_b3_antar_perusahaan_hanya_grup_sendiri():
    s = _src("routers/intercompany.py")
    assert "consolidation_entities ce JOIN consolidation_groups cg" in s and "WHERE cg.tenant_id = $1 AND ce.entity_tenant_id = $2" in s


def test_b4_members_tanpa_tenant_terpaku():
    s = _src("routers/members.py")
    assert '"evlogia")' not in s and s.count("tenant_id = _tenant(request)") == 4


def test_b8_waf_linear():
    from app.middleware.waf_middleware import WAFMiddleware as W
    semua = [p for nama in dir(W) if nama.endswith("_PATTERNS") for p in getattr(W, nama) if isinstance(p, re.Pattern)]
    assert len(semua) > 20
    n = 256 * 1024
    for badan in ("SELECT " * (n // 7), "<script " * (n // 8), "<img " * (n // 5), "<svg " * (n // 5), "$(" * (n // 2), "/*" * (n // 2)):
        t = time.time()
        for p in semua:
            p.search(badan)
        assert time.time() - t < 2.0, badan[:8]
    assert W.MAX_INSPEKSI_BODY == 1024 * 1024
    # deteksi tetap
    assert any(p.search("1 UNION SELECT a FROM b") for p in semua) and any(p.search("<script>alert(1)</script>") for p in semua)
