"""LAMPIRAN-PEMBELIAN F2a (30 Okt 2026) -- lampiran Pesanan Pembelian + Nota Debit lewat pabrik lampiran_induk (POST/GET/unduh/DELETE, izin
per modul: purchase_order / debit_note, pola NK: tambah = U) + keluarga /api/lampiran/{purchase_order|vendor_credit}: PO <-> FPB lewat
bills.purchase_order_id, ND <-> FPB lewat original_bill_id, satu langkah dari benih; urutan jenis PO, FPB, PK, ND. entity_type
'vendor_credit' (bukan 'credit_note' milik NK). Bukti DB nyata (kaos ROLLBACK, dua arah, NK tak bocor, lintas tenant 404):
scripts/ops/uji-nyata-lampiran-f2a.py."""
import re
import uuid

import pytest

from app.middleware import permission_middleware as pm
from app.routers import lampiran_dokumen as LD
from app.routers import lampiran_induk as LI
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_so_keluarga import _pemenang

DOC, AID = uuid.uuid4(), uuid.uuid4()
SPEC = [(LI.SPEC_PESANAN_PEMBELIAN, "purchase_order"), (LI.SPEC_NOTA_DEBIT, "debit_note")]
SQL = " ".join(LD.SQL_KELUARGA_BELI.split())


def test_spesifikasi_entitas_tabel_nomor():
    assert (LI.SPEC_PESANAN_PEMBELIAN.entitas, LI.SPEC_PESANAN_PEMBELIAN.modul_url, LI.SPEC_PESANAN_PEMBELIAN.tabel,
            LI.SPEC_PESANAN_PEMBELIAN.kolom_nomor) == ("purchase_order", "purchase-orders", "purchase_orders", "po_number")
    assert (LI.SPEC_NOTA_DEBIT.entitas, LI.SPEC_NOTA_DEBIT.modul_url, LI.SPEC_NOTA_DEBIT.tabel,
            LI.SPEC_NOTA_DEBIT.kolom_nomor) == ("vendor_credit", "vendor-credits", "vendor_credits", "credit_number")


@pytest.mark.parametrize("s,modul", SPEC, ids=["po", "nd"])
def test_rute_dimenangkan_pabrik_dan_izin_per_metode(s, modul):
    base = f"/api/{s.modul_url}/{DOC}/attachments"
    for path, metode, nama, aksi in ((base, "POST", "unggah", "U"), (base, "GET", "daftar", "R"),
                                     (f"{base}/{AID}/download", "GET", "unduh", "R"), (f"{base}/{AID}", "DELETE", "lepas", "D")):
        r = _pemenang(path, metode)
        assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == "lampiran_induk", (path, metode)
        assert r.endpoint.__name__ == f"{nama}_lampiran_{s.entitas}"
        assert pm.izin_untuk_jalur(path, metode) == (modul, aksi), (path, metode)


@pytest.mark.parametrize("s,_m", SPEC, ids=["po", "nd"])
def test_sql_pabrik_bertenant_dan_entitas_tetap(s, _m):
    for k, sql in LI.sql_spec(s).items():
        for tabel, alias in re.findall(r"\b(?:FROM|JOIN|USING)\s+(\w+)\s+(\w+)", sql):
            if alias.upper() in {"WHERE", "ON", "SET"}:
                assert re.search(r"\btenant_id\s*=\s*\$\d", sql), (k, tabel)
            else:
                assert re.search(rf"\b{alias}\.tenant_id\s*=\s*\$\d", sql), (k, tabel, alias)
        if "document_attachments" in sql:
            assert f"'{s.entitas}'" in sql, k


@pytest.mark.parametrize("jenis,modul", [("purchase_order", "purchase_order"), ("vendor_credit", "debit_note")])
def test_rute_lampiran_keluarga_dan_izin_grup(jenis, modul):
    r = _pemenang(f"/api/lampiran/{jenis}/{DOC}")
    assert r is not None and r.endpoint.__name__ == "lampiran_dokumen"
    assert pm.izin_untuk_jalur(f"/api/lampiran/{jenis}/{DOC}") == (modul, "R")
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis(jenis)) == (modul, "R")


def test_urutan_jenis_pembelian():
    beli = [j for j in SL.URUTAN_JENIS if j in LD.JENIS_PEMBELIAN]
    assert beli == ["purchase_order", "vendor_deposit", "goods_receipt", "bill", "payment_out", "vendor_credit"]   # F2b: + UMP, GRN


def test_keluarga_satu_langkah_dari_benih():
    assert "o0x.id = $1 AND $3 = 'purchase_order'" in SQL and "n0x.id = $1 AND $3 = 'vendor_credit'" in SQL
    # FPB: dari PO benih (purchase_order_id) dan ND benih (original_bill_id)
    assert "bx.purchase_order_id IN (SELECT id FROM po0)" in SQL and "bx.id IN (SELECT original_bill_id FROM nd0)" in SQL
    # PO dari FPB benih; ND dari FPB benih -- hanya benih (fb0), bukan keluarga (fb) -> satu langkah
    assert "b1x.tenant_id = $2 AND b1x.id IN (SELECT id FROM fb0)" in SQL and "vx.original_bill_id IN (SELECT id FROM fb0)" in SQL
    assert "IN (SELECT id FROM fb))" not in SQL.split("), meta AS")[0].split("), po AS")[1]


def test_grup_baru_entity_type_dan_tenant():
    assert "SELECT 'purchase_order', 'purchase_order', o.id" in SQL and "FROM purchase_orders o WHERE o.tenant_id = $2" in SQL
    assert "SELECT 'vendor_credit', 'vendor_credit', v.id" in SQL and "FROM vendor_credits v WHERE v.tenant_id = $2" in SQL
    assert "credit_note" not in SQL and "credit_notes" not in SQL   # NK tak pernah ikut


@pytest.mark.parametrize("path,metode,modul,nama", [
    (f"/api/purchase-orders/{DOC}", "GET", "purchase_orders", "get_purchase_order"),
    (f"/api/purchase-orders/{DOC}", "PATCH", "purchase_orders", None),
    (f"/api/purchase-orders/{DOC}", "DELETE", "purchase_orders", None),
    (f"/api/vendor-credits/{DOC}", "GET", "vendor_credits", "get_vendor_credit"),
    (f"/api/vendor-credits/{DOC}", "DELETE", "vendor_credits", None),
])
def test_rute_lama_modul_tetap_dimenangkan_modulnya(path, metode, modul, nama):
    """Pabrik lampiran didaftarkan SEBELUM router modul: /{id} lama TETAP milik modulnya (bukan pabrik)."""
    r = _pemenang(path, metode)
    assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == modul, (path, metode, getattr(r, "endpoint", None))
    if nama:
        assert r.endpoint.__name__ == nama
