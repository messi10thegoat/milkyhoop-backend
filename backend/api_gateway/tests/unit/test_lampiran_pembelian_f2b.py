"""LAMPIRAN-PEMBELIAN F2b (31 Okt 2026) -- lampiran Penerimaan Barang (GRN) + Uang Muka Pembelian (UMP) lewat pabrik lampiran_induk (izin GRN =
modul purchase_order, UMP = vendor_deposit; U/R/R/D) + keluarga /api/lampiran/{goods_receipt|vendor_deposit}: GRN -> PO-nya + FPB bertaut AKTIF
(goods_receipt_bill_links); UMP -> PO-nya + FPB yang diterapkan AKTIF (vendor_deposit_applications); PO / FPB -> GRN & UMP terkait. Satu langkah
dari benih; urutan PO, UMP, GRN, FPB, PK, ND. Migrasi V466 menambah goods_receipt + vendor_deposit ke chk_da_entity (pembalik gagal keras bila
sudah dipakai). Bukti DB nyata (kaos ROLLBACK + uji migrasi DB sekali-pakai): scripts/ops/uji-nyata-lampiran-f2b.py, uji-migrasi-v466.sh."""
import pathlib
import re
import uuid

import pytest

from app.middleware import permission_middleware as pm
from app.routers import lampiran_dokumen as LD
from app.routers import lampiran_induk as LI
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_so_keluarga import _pemenang

DOC, AID = uuid.uuid4(), uuid.uuid4()
SPEC = [(LI.SPEC_PENERIMAAN_BARANG, "purchase_order"), (LI.SPEC_UANG_MUKA_PEMBELIAN, "vendor_deposit")]
SQL = " ".join(LD.SQL_KELUARGA_BELI.split())
MIG = pathlib.Path(__file__).resolve().parents[3] / "migrations"


def test_spesifikasi_entitas_tabel_nomor():
    g, u = LI.SPEC_PENERIMAAN_BARANG, LI.SPEC_UANG_MUKA_PEMBELIAN
    assert (g.entitas, g.modul_url, g.tabel, g.kolom_nomor) == ("goods_receipt", "goods-receipts", "goods_receipts", "receipt_number")
    assert (u.entitas, u.modul_url, u.tabel, u.kolom_nomor) == ("vendor_deposit", "vendor-deposits", "vendor_deposits", "deposit_number")


@pytest.mark.parametrize("s,modul", SPEC, ids=["grn", "ump"])
def test_rute_dimenangkan_pabrik_dan_izin_per_metode(s, modul):
    base = f"/api/{s.modul_url}/{DOC}/attachments"
    for path, metode, nama, aksi in ((base, "POST", "unggah", "U"), (base, "GET", "daftar", "R"),
                                     (f"{base}/{AID}/download", "GET", "unduh", "R"), (f"{base}/{AID}", "DELETE", "lepas", "D")):
        r = _pemenang(path, metode)
        assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == "lampiran_induk", (path, metode)
        assert r.endpoint.__name__ == f"{nama}_lampiran_{s.entitas}"
        assert pm.izin_untuk_jalur(path, metode) == (modul, aksi), (path, metode)


@pytest.mark.parametrize("path,metode,modul", [
    (f"/api/goods-receipts/{DOC}", "GET", "goods_receipts"), (f"/api/goods-receipts/{DOC}/documents", "GET", "goods_receipts"),
    (f"/api/vendor-deposits/{DOC}", "GET", "vendor_deposits"), (f"/api/vendor-deposits/{DOC}/documents", "GET", "vendor_deposits"),
])
def test_rute_lama_modul_tetap_dimenangkan_modulnya(path, metode, modul):
    r = _pemenang(path, metode)
    assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == modul, (path, getattr(r, "endpoint", None))


@pytest.mark.parametrize("s,_m", SPEC, ids=["grn", "ump"])
def test_sql_pabrik_bertenant_dan_entitas_tetap(s, _m):
    for k, sql in LI.sql_spec(s).items():
        for tabel, alias in re.findall(r"\b(?:FROM|JOIN|USING)\s+(\w+)\s+(\w+)", sql):
            if alias.upper() in {"WHERE", "ON", "SET"}:
                assert re.search(r"\btenant_id\s*=\s*\$\d", sql), (k, tabel)
            else:
                assert re.search(rf"\b{alias}\.tenant_id\s*=\s*\$\d", sql), (k, tabel, alias)
        if "document_attachments" in sql:
            assert f"'{s.entitas}'" in sql, k


@pytest.mark.parametrize("jenis,modul", [("goods_receipt", "purchase_order"), ("vendor_deposit", "vendor_deposit")])
def test_rute_lampiran_keluarga_dan_izin_grup(jenis, modul):
    r = _pemenang(f"/api/lampiran/{jenis}/{DOC}")
    assert r is not None and r.endpoint.__name__ == "lampiran_dokumen"
    assert pm.izin_untuk_jalur(f"/api/lampiran/{jenis}/{DOC}") == (modul, "R")
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis(jenis)) == (modul, "R")


def test_urutan_jenis_pembelian():
    assert [j for j in SL.URUTAN_JENIS if j in LD.JENIS_PEMBELIAN] == ["purchase_order", "vendor_deposit", "goods_receipt", "bill",
                                                                         "payment_out", "vendor_credit"]


def test_keluarga_grn_ump_satu_langkah_tautan_aktif():
    assert "g0x.id = $1 AND $3 = 'goods_receipt'" in SQL and "u0x.id = $1 AND $3 = 'vendor_deposit'" in SQL
    # GRN benih -> FPB bertaut AKTIF; FPB benih -> GRN bertaut AKTIF (bukan keluarga fb)
    assert "WHERE l0x.status = 'active' AND gi0x.goods_receipt_id IN (SELECT id FROM gr0)" in SQL
    assert "WHERE l1x.status = 'active' AND bi1x.bill_id IN (SELECT id FROM fb0)" in SQL
    # UMP benih -> FPB diterapkan AKTIF; FPB benih -> UMP diterapkan AKTIF
    assert "WHERE a2x.status = 'active' AND a2x.vendor_deposit_id IN (SELECT id FROM um0)" in SQL
    assert "WHERE a3x.status = 'active' AND a3x.bill_id IN (SELECT id FROM fb0)" in SQL
    # PO benih -> GRN & UMP ber-PO; GRN/UMP benih -> PO-nya
    assert "gx.purchase_order_id IN (SELECT id FROM po0)" in SQL and "ux.purchase_order_id IN (SELECT id FROM po0)" in SQL
    assert "OR ox.id IN (SELECT purchase_order_id FROM gr0) OR ox.id IN (SELECT purchase_order_id FROM um0)" in SQL


def test_grup_baru_entity_type_dan_tenant():
    assert "SELECT 'goods_receipt', 'goods_receipt', g.id" in SQL and "FROM goods_receipts g WHERE g.tenant_id = $2" in SQL
    assert "SELECT 'vendor_deposit', 'vendor_deposit', u.id" in SQL and "FROM vendor_deposits u WHERE u.tenant_id = $2" in SQL
    assert "customer_deposit" not in SQL and "credit_note" not in SQL   # UM pelanggan / NK tak pernah ikut
    assert "gx.tenant_id = $2" in SQL and "ux.tenant_id = $2" in SQL


def test_migrasi_v466_hanya_menambah_dan_pembalik_gagal_keras():
    naik = (MIG / "V466__chk_da_entity_grn_ump.sql").read_text()
    turun = (MIG / "V466__chk_da_entity_grn_ump_ROLLBACK.sql").read_text()
    nilai = lambda t: set(re.findall(r"'([a-z_]+)'", t.split("CHECK (entity_type IN (")[1].split("))")[0]))
    v268 = nilai((MIG / "V268__attach_proforma_delivery.sql").read_text())
    assert nilai(naik) == v268 | {"goods_receipt", "vendor_deposit"} and len(v268) == 25
    assert nilai(turun) == v268
    assert "RAISE EXCEPTION" in turun and "entity_type IN ('goods_receipt','vendor_deposit')" in turun
    assert turun.index("RAISE EXCEPTION") < turun.index("DROP CONSTRAINT")
