"""P2P-1 (25 Okt 2026, MASTER): rute tagihan v1 tanpa pemakai DIPARKIR (409 FEATURE_NOT_AVAILABLE sebelum badan handler) dan kode mati
ActionService.execute_action dijaga NOL pemanggil. Bukti nol pemakai (3 sumber, dicatat di pesan commit): FE origin/main src, BE
registri/agen/milkybook/scripts e2e, log akses nginx 11-25 Okt (kontrol pola menemukan rute v2)."""
import ast
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import bills as B
from app.services import fitur_parkir as FP

APP = Path(B.__file__).resolve().parents[1]
PARKIR = {("/api/bills", "POST"), ("/api/bills/{bill_id}", "PATCH"), ("/api/bills/{bill_id}/mark-paid", "PATCH")}
HIDUP = {("/api/bills/v2", "POST"), ("/api/bills/v2/{bill_id}", "PATCH"), ("/api/bills/{bill_id}", "DELETE"),
         ("/api/bills/{bill_id}/post", "POST"), ("/api/bills/{bill_id}/void", "POST"), ("/api/bills/{bill_id}/payments", "POST")}


def _parkir(route) -> bool:
    return any(d.call is FP.fitur_belum_tersedia for d in route.dependant.dependencies)


def _rute():
    out = {}
    for r in B.router.routes:
        for m in getattr(r, "methods", ()) or ():
            p = r.path if r.path.startswith("/api/bills") else "/api/bills" + r.path
            out[(p.rstrip("/") if p != "/api/bills/" else "/api/bills", m)] = r
    return out


def test_rute_v1_diparkir_409_dan_pengganti_hidup():
    rute = _rute()
    for k in PARKIR:
        assert k in rute and _parkir(rute[k]), k
    for k in HIDUP:
        assert k in rute and not _parkir(rute[k]), k
    with pytest.raises(HTTPException) as e:
        asyncio.run(FP.fitur_belum_tersedia())
    assert e.value.status_code == 409 and e.value.detail["code"] == "FEATURE_NOT_AVAILABLE"


def _pemanggil(nama: str) -> list:
    hasil = []
    for p in APP.rglob("*.py"):
        src = p.read_text(errors="ignore")
        if nama not in src:
            continue
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == nama:
                hasil.append(f"{p.relative_to(APP)}:{n.lineno}")
    return hasil


def test_kode_mati_action_service_nol_pemanggil():
    assert _pemanggil("execute_action") == [], "ActionService.execute_action KODE MATI -- pemanggil baru muncul"
    # _execute_create_purchase_invoice hanya dipanggil dari execute_action (yang mati)
    assert all(x.startswith("services/action_service.py") for x in _pemanggil("_execute_create_purchase_invoice"))


def test_kontrol_pemindai_pemanggil_bisa_gagal():
    """Kontrol: pemindai menemukan pemanggil nyata (bukan selalu kosong)."""
    assert _pemanggil("fitur_belum_tersedia_dengan") or _pemanggil("simpan"), "pemindai buta"
    assert any(x.startswith("routers/") for x in _pemanggil("mulai_aksi"))
