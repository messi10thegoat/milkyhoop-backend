"""Pola izin #53 "Lepas" alokasi (receive_payment V) + reklas persediaan
(journal C) + penjaga rute yang MEMBACA WRITE_EXEMPT (24 Sep 2026).

Latar (diukur dengan dispatch sungguhan, COLLABORATOR kaos): kedua rute tulis
ini TANPA pola -> cabang default-tertutup: staf 403 PERMISSION_UNMAPPED, OWNER
lolos. Bukan celah, tapi #53 MATI untuk staf. Putusan MASTER: unapply = V
(menulis jurnal pembalik, setara void); reklas = journal C dengan handler
OWNER-only.

Tes dispatch memakai PermissionMiddleware SUNGGUHAN + PolicyEngineClient.can()
SUNGGUHAN (termasuk bypass OWNER, override > peran, fail-closed); yang
dipalsukan hanya pembacaan DB (konteks pengguna, izin peran, override).
"""
import asyncio
import re
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.middleware import permission_middleware as pm
from backend.api_gateway.app.services import policy_engine_client as pec

U = "11111111-1111-1111-1111-111111111111"
T = "kaos-biru-konveksi"
UNAPPLY = f"/api/receive-payments/{U}/allocations/{U}/unapply"
REKLAS = "/api/journals/reclassify-bill-inventory"
TESTS = Path(__file__).parent


def _mw():
    return pm.PermissionMiddleware(app=None)


# ----------------------------------------------------------- pemetaan
def test_unapply_dipetakan_receive_payment_v():
    assert _mw()._find_permission(UNAPPLY, "POST") == ("receive_payment", "V")


def test_reklas_dipetakan_journal_c():
    assert _mw()._find_permission(REKLAS, "POST") == ("journal", "C")


def test_rute_rp_lama_tak_bergeser():
    mw = _mw()
    assert mw._find_permission(f"/api/receive-payments/{U}/void", "POST") == ("receive_payment", "V")
    assert mw._find_permission(f"/api/receive-payments/{U}/post", "POST") == ("receive_payment", "P")
    assert mw._find_permission("/api/journals", "POST") == ("journal", "C")


# ----------------------------------------------------------- dispatch + can() sungguhan
class Engine(pec.PolicyEngineClient):
    """can() ASLI; hanya sumber data DB yang diganti."""

    def __init__(self, peran, izin_peran, override=None):
        super().__init__(pool=None)
        self.peran, self.izin_peran, self.override = peran, izin_peran, override or {}

    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return pec.UserContext(
            user_id=user_id, tenant_id=tenant_id, subscription_role=subscription_role,
            business_role_id="peran-1", business_role_code=self.peran, membership_active=True,
        )

    async def _get_role_permissions(self, role_id):
        return self.izin_peran

    async def _get_user_overrides(self, user_id, tenant_id):
        return self.override


def _jalankan(monkeypatch, engine, path):
    monkeypatch.setattr(pm, "get_policy_engine", lambda: engine)
    scope = {"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b"",
             "state": {"user": {"user_id": U, "tenant_id": T, "role": "USER"}}}
    dipanggil = []

    async def call_next(r):
        dipanggil.append(1)
        return Response("HANDLER", status_code=299)

    resp = asyncio.run(_mw().dispatch(Request(scope), call_next))
    return resp.status_code, bool(dipanggil), resp.body


RP = pec.normalize_module_name("receive_payment")
JR = pec.normalize_module_name("journal")


def test_staf_ber_v_lolos_unapply(monkeypatch):
    st, lolos, _ = _jalankan(monkeypatch, Engine("STAFF", {RP: ["R", "V"]}), UNAPPLY)
    assert (st, lolos) == (299, True)


def test_staf_tanpa_v_403_unapply(monkeypatch):
    st, lolos, body = _jalankan(monkeypatch, Engine("STAFF", {RP: ["R", "C", "U", "P"]}), UNAPPLY)
    assert (st, lolos) == (403, False)
    assert b"PERMISSION_DENIED" in body and b"receive_payment" in body
    assert b"PERMISSION_UNMAPPED" not in body  # kini terpetakan, bukan default-tertutup


def test_override_mencabut_v_menang_atas_peran(monkeypatch):
    st, lolos, _ = _jalankan(monkeypatch, Engine("STAFF", {RP: ["V"]}, override={RP: ["R"]}), UNAPPLY)
    assert (st, lolos) == (403, False)


def test_owner_lolos_unapply(monkeypatch):
    st, lolos, _ = _jalankan(monkeypatch, Engine("OWNER", {}), UNAPPLY)
    assert (st, lolos) == (299, True)


def test_staf_journal_c_lolos_middleware_reklas(monkeypatch):
    # Lapis pertama saja; handler reklas tetap OWNER-only (journals.py).
    st, lolos, _ = _jalankan(monkeypatch, Engine("STAFF", {JR: ["C"]}), REKLAS)
    assert (st, lolos) == (299, True)
    st, lolos, _ = _jalankan(monkeypatch, Engine("STAFF", {JR: ["R"]}), REKLAS)
    assert (st, lolos) == (403, False)


def test_handler_reklas_owner_only_tetap_ada():
    src = (TESTS.parents[1] / "app" / "routers" / "journals.py").read_text()
    i = src.index("async def reclassify_bill_inventory_endpoint")
    badan = src[i:i + 1500]
    assert 'business_role_code", None) != "OWNER"' in badan
    assert "status_code=403" in badan


# ----------------------------------------------------------- penjaga membaca WRITE_EXEMPT
def _parse_exempt():
    teks = (TESTS.parents[1] / "app" / "middleware" / "permission_middleware.py").read_text()
    blok = re.search(r"WRITE_EXEMPT\s*=\s*\[(.*?)\n\]", teks, re.S).group(1)
    return re.findall(r"\(\s*r[\"']([^\"']+)[\"']\s*,", blok)


def test_pengurai_write_exempt_sama_dengan_modul():
    """Penjaga statis (tes pagar + gate_izin_rute) mengurai berkas; ia harus
    melihat PERSIS daftar yang dipakai middleware."""
    assert _parse_exempt() == [p for p, _ in pm.WRITE_EXEMPT]
    assert len(pm.WRITE_EXEMPT) >= 10


def test_kontrol_merah_chat_hanya_tertutup_lewat_exempt():
    """Tanpa membaca WRITE_EXEMPT, POST /{tenant_id}/chat = 'tanpa pola'
    (persis salah hitung gate 24 Sep). Dengan membacanya, tercakup."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pagar_rute", TESTS / "test_pagar_rute_izin.py")
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)

    inv = set(P._baris(P.INVENTARIS))
    assert "POST /{tenant_id}/chat" in inv
    assert "POST /{tenant_id}/chat" not in P._tanpa_pola()
    # sisi merah: pengurai exempt dimatikan -> rute kembali terhitung
    asli = P._write_exempt
    try:
        P._write_exempt = lambda: []
        assert "POST /{tenant_id}/chat" in P._tanpa_pola()
    finally:
        P._write_exempt = asli
