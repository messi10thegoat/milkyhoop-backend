"""LAMPIRAN-TANPA-SO (15 Okt 2026, putusan MASTER) -- GET /api/lampiran/{jenis}/{id}.

Yang dijaga:
  * rute dimenangkan handler lampiran_dokumen; izin rute = R modul jenisnya (SJ = modul faktur); jenis lain tak terpetakan;
  * SATU SQL berbenih: _SQL_KELUARGA / _SQL_ANGGOTA_KELUARGA berbagi _SQL_META_KELUARGA; SQL calon SO + pemilik bertenant per alias;
  * tepat satu SO TERVERIFIKASI -> delegasi ke keluarga SO (benih kosong); nol / lebih dari satu -> benih dokumen ini, SO NULL;
  * calon SO yang GAGAL verifikasi keanggotaan tidak dipakai (usulan bukan putusan);
  * penerimaan dicari di receive_payments (id bill_payments_v2 -> 404) tanpa kueri keluarga; jenis asing -> 404;
  * izin grup dicabut -> {jenis, tersembunyi_izin, jumlah} (jalur susun yang SAMA dengan SO).
"""
import asyncio
import os
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.middleware import permission_middleware as pm
from app.routers import lampiran_dokumen as LD
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_so_keluarga import PEPalsu, _pemenang, _tabel_tanpa_tenant, baris

T = "kaos-biru-konveksi"
SO1, SO2, DOK, INV, DP = (uuid.uuid4() for _ in range(5))


# ------------------------------------------------------------------ rute + izin
def test_rute_dimenangkan_handler_lampiran_dokumen():
    r = _pemenang(f"/api/lampiran/sales_invoice/{INV}")
    assert r is not None and r.endpoint.__name__ == "lampiran_dokumen" and r.path == "/api/lampiran/{jenis}/{dok_id}"


@pytest.mark.parametrize("jenis,harap", [
    ("sales_order", ("sales_order", "R")), ("sales_invoice", ("sales_invoice", "R")),
    ("receive_payment", ("receive_payment", "R")), ("customer_deposit", ("customer_deposit", "R")),
    ("proforma", ("proforma", "R")), ("delivery", ("sales_invoice", "R")), ("credit_note", ("credit_note", "R")),
    ("quote", ("quote", "R")),
])
def test_izin_rute_per_jenis(jenis, harap):
    assert pm.izin_untuk_jalur(f"/api/lampiran/{jenis}/{DOK}") == harap


def test_jenis_lain_tak_terpetakan_dan_semua_jenis_dokumen_berizin():
    assert pm.izin_untuk_jalur(f"/api/lampiran/bill_payment/{DOK}") is None
    assert set(LD.JENIS_DOKUMEN) == set(LD.SQL_CALON_SO)
    for j in LD.JENIS_DOKUMEN:
        assert pm.izin_untuk_jalur(f"/api/lampiran/{j}/{DOK}") is not None, j


# ------------------------------------------------------------------ SQL
def test_satu_sql_berbenih_bukan_salinan():
    assert SL._SQL_KELUARGA.startswith(SL._SQL_META_KELUARGA)
    assert SL._SQL_ANGGOTA_KELUARGA.startswith(SL._SQL_META_KELUARGA)
    for n in range(3, 10):
        assert f"ANY(${n}::uuid[])" in SL._SQL_META_KELUARGA, n
    assert SL.param_keluarga(SO1, T) == (SO1, T, [], [], [], [], [], [], [])
    assert SL.param_keluarga(None, T, {"receive_payment": [DOK]})[4] == [DOK]
    assert SL.BENIH_JENIS == ("sales_invoice", "customer_deposit", "receive_payment", "proforma", "delivery", "credit_note", "quote")
    assert "FROM receive_payments rp0x" in SL._SQL_META_KELUARGA     # benih penerimaan hanya id receive_payments


def test_predikat_keluarga_lama_utuh_dan_benih_penerimaan_tanpa_alokasi():
    """Data nyata 15 Okt: 0 DP yang ke SO hanya lewat penawaran, 0 penerimaan tanpa alokasi/deposit -> pasangan nyata buta;
    predikatnya dijaga di sini (+ K3/K4 harness di tx-ROLLBACK)."""
    m = SL._SQL_META_KELUARGA
    for p in ("cdx.sales_order_id IN (SELECT id FROM so)", "cdx.quote_id IN (SELECT quote_id FROM so WHERE quote_id IS NOT NULL)",
              "cdx.proforma_id IN (SELECT id FROM pf)", "rpx.source_deposit_id IN (SELECT id FROM dp)",
              "rpx.created_deposit_id IN (SELECT id FROM dp)", "alx.invoice_id IN (SELECT id FROM si)",
              "six.sales_order_id IN (SELECT id FROM so)", "pfx.sales_order_id IN (SELECT id FROM so)",
              "COALESCE(f.sales_order_id, fsi.sales_order_id) IN (SELECT id FROM so)",
              "OR rpx.id IN (SELECT id FROM rp0)", "OR cdx.id IN (SELECT id FROM dp0)", "OR f.id IN (SELECT id FROM sj0)",
              "OR n.id IN (SELECT id FROM nk0)", "OR pfx.id = ANY($6::uuid[])", "OR six.id = ANY($3::uuid[])"):
        assert p in m, p


def test_sql_anggota_dan_calon_bertenant_per_alias():
    assert _tabel_tanpa_tenant(SL._SQL_ANGGOTA_KELUARGA) == []
    for j, sql in LD.SQL_CALON_SO.items():
        for tabel, alias in re.findall(r"\b(?:FROM|JOIN)\s+(\w+)\s+(\w+)", sql):
            if alias.upper() in {"WHERE", "ON"}:
                assert re.search(rf"FROM\s+{tabel}\s+WHERE\s+[^)]*\btenant_id\s*=\s*\$2", sql), (j, tabel)
            else:
                assert re.search(rf"\b{alias}\.tenant_id\s*=\s*\$2", sql), (j, tabel, alias)


# ------------------------------------------------------------------ handler (DB palsu)
class Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, ada=True, calon=(), anggota=(), rows=()):
        self.ada, self.calon, self.anggota, self.rows = ada, list(calon), set(anggota), list(rows)
        self.keluarga = []

    def transaction(self):
        return Txn()

    async def execute(self, sql, *a):
        return None

    async def fetchrow(self, sql, *a):
        return {"nomor": "INV-X"} if self.ada else None

    async def fetchval(self, sql, *a):
        if sql == SL._SQL_ANGGOTA_KELUARGA:
            return a[0] in self.anggota
        if "order_number FROM sales_orders" in sql:
            return "SO-X"
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        if sql == SL._SQL_KELUARGA:
            self.keluarga.append(a)
            return self.rows
        return [(s,) for s in self.calon]


class Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def jalankan(monkeypatch, conn, jenis="sales_invoice", did=None, pe=None):
    async def gp():
        return SimpleNamespace(acquire=lambda: Acq(conn))
    monkeypatch.setattr(SL, "get_pool", gp)
    monkeypatch.setattr(pm, "get_policy_engine", lambda: pe or PEPalsu())
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "u1", "tenant_id": T, "role": "ADMIN"}))
    return asyncio.run(LD.lampiran_dokumen(req, jenis, str(did or DOK)))


def test_satu_so_terverifikasi_delegasi_keluarga_so(monkeypatch):
    c = Conn(calon=[SO1], anggota={SO1})
    out = jalankan(monkeypatch, c)
    assert c.keluarga == [(SO1, T, [], [], [], [], [], [], [])]
    assert out["sumber"] == {"jenis": "sales_invoice", "id": str(DOK), "nomor": "INV-X", "mode": "keluarga_so",
                             "sales_order": {"id": str(SO1), "nomor": "SO-X"}}


def test_tanpa_so_benih_dokumen(monkeypatch):
    c = Conn(calon=[], rows=[baris("sales_invoice", DOK, "INV-X", uuid.uuid4(), "nota.pdf")])
    out = jalankan(monkeypatch, c, jenis="receive_payment")
    assert c.keluarga == [(None, T, [], [], [DOK], [], [], [], [])]
    assert out["sumber"]["mode"] == "dokumen" and out["sumber"]["sales_order"] is None
    assert out["jumlah"] == 1 and out["data"][0]["dokumen"]["jenis"] == "sales_invoice"


def test_dua_so_atau_calon_gagal_verifikasi_benih_dokumen(monkeypatch):
    c = Conn(calon=[SO1, SO2], anggota={SO1, SO2})
    assert jalankan(monkeypatch, c, jenis="receive_payment")["sumber"]["mode"] == "dokumen"
    c2 = Conn(calon=[SO1], anggota=set())          # usulan calon ditolak keanggotaan keluarga
    assert jalankan(monkeypatch, c2)["sumber"]["mode"] == "dokumen" and c2.keluarga[0][0] is None


def test_dokumen_tak_ada_di_tabel_pemilik_404_tanpa_kueri_keluarga(monkeypatch):
    c = Conn(ada=False)
    with pytest.raises(HTTPException) as e:
        jalankan(monkeypatch, c, jenis="receive_payment")
    assert e.value.status_code == 404 and c.keluarga == []
    assert LD.JENIS_DOKUMEN["receive_payment"][0] == "receive_payments"


def test_jenis_asing_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        jalankan(monkeypatch, Conn(), jenis="bill_payment")
    assert e.value.status_code == 404


def test_izin_grup_dicabut_tersembunyi(monkeypatch):
    rows = [baris("customer_deposit", DP, "DEP-1", uuid.uuid4(), "bukti.png"),
            baris("sales_invoice", DOK, "INV-X", uuid.uuid4(), "nota.pdf")]
    out = jalankan(monkeypatch, Conn(calon=[], rows=rows), pe=PEPalsu(tolak={"customer_deposit"}))
    assert {"dokumen": {"jenis": "customer_deposit"}, "tersembunyi_izin": True, "jumlah": 1} in out["data"]
    assert "bukti.png" not in repr(out) and "DEP-1" not in repr(out)


def test_penanda():
    assert LD.PENANDA_LAMPIRAN_DOKUMEN == "lampiran-dokumen-tanpa-so-w20261015"
