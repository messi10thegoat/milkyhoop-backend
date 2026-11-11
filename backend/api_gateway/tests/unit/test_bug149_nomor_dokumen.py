"""BUG-149 (putusan pemilik 12 Nov 2026): nomor dokumen = SATU aturan untuk Faktur, SO, Penawaran, Tagihan, PO.
- pratinjau nomor berikutnya (perkiraan) TANPA memakan seri, meniru generator (melompati nomor terpakai);
- nomor manual: trim + format SAMA untuk kelima dokumen; terpakai -> 409 {code, message} dari server;
- balapan serentak (UNIQUE pada indeks nomor) -> 409 yang sama; pelanggaran unik lain TIDAK disamarkan jadi 'nomor terpakai';
- seri otomatis tak pernah dinaikkan oleh nomor manual."""
import asyncio
import inspect
import re

import pytest
from fastapi import HTTPException

from app.services import nomor_dokumen as ND


class _K:
    """Koneksi tiruan: seri (last_number), himpunan nomor terpakai, jurnal SQL (untuk membuktikan nol tulis)."""

    def __init__(self, last=None, terpakai=(), yymm="2611"):
        self.last, self.terpakai, self.yymm, self.sql = last, set(terpakai), yymm, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {"ym": f"20{self.yymm[:2]}-{self.yymm[2:]}", "yymm": self.yymm}

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "generate_" in sql:
            return "GENERATED"
        if "_sequences" in sql:
            return self.last
        return 1 if a[1] in self.terpakai else None  # a = (tenant, nomor[, kecuali_id])

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return [{"n": x} for x in sorted(self.terpakai)]


def _tulis(k):
    return [s for s in k.sql if re.search(r"\b(INSERT|UPDATE|DELETE)\b", s, re.I)]


def test_bersihkan_trim_format_dan_kosong():
    assert ND.bersihkan(None) is None and ND.bersihkan("") is None and ND.bersihkan("   ") is None
    assert ND.bersihkan("  INV/2611.0030-A ") == "INV/2611.0030-A"
    for jelek in ("INV 0030", "INV#1", "x" * 51, "nomor\tbaru"):
        with pytest.raises(HTTPException) as e:
            ND.bersihkan(jelek)
        assert e.value.status_code == 400


def test_tetapkan_manual_bebas_terpakai_dan_kosong():
    k = _K(terpakai={"INV-2611-0030"})
    assert asyncio.run(ND.tetapkan_nomor(k, "t", "sales_invoice", " NOTA-77 ")) == "NOTA-77"
    with pytest.raises(HTTPException) as e:
        asyncio.run(ND.tetapkan_nomor(k, "t", "sales_invoice", "INV-2611-0030"))
    assert e.value.status_code == 409
    assert e.value.detail == {"code": "NOMOR_DOKUMEN_TERPAKAI",
                              "message": "Nomor INV-2611-0030 sudah dipakai di tenant ini. Pakai nomor lain atau kosongkan untuk nomor otomatis."}
    assert asyncio.run(ND.tetapkan_nomor(k, "t", "sales_invoice", None)) == "GENERATED"
    assert any("generate_sales_invoice_number" in s for s in k.sql)


def test_tetapkan_manual_tidak_menyentuh_seri():
    k = _K(last=29)
    asyncio.run(ND.tetapkan_nomor(k, "t", "quote", "QUO-2611-0030"))
    assert not _tulis(k) and not any("generate_" in s for s in k.sql)


def test_ubah_draf_nomor_milik_sendiri_boleh_nomor_orang_lain_409():
    k = _K(terpakai={"SO-2611-0005"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(ND.pastikan_bebas(k, "t", "sales_order", "SO-2611-0005", kecuali_id="id-lain"))
    assert e.value.status_code == 409
    assert any("id <> $3" in s for s in k.sql)  # milik dokumen yang sedang diubah tak dihitung bentrok


@pytest.mark.parametrize("jenis,awalan,tabel", [
    ("sales_invoice", "INV", "sales_invoices"), ("sales_order", "SO", "sales_orders"), ("quote", "QUO", "quotes"),
    ("bill", "PB", "bills"), ("purchase_order", "PO", "purchase_orders")])
def test_pratinjau_format_seri_kosong_dan_nol_tulis(jenis, awalan, tabel):
    k = _K(last=None)
    assert asyncio.run(ND.pratinjau(k, "t", jenis)) == f"{awalan}-2611-0001"
    assert not _tulis(k)
    assert ND.JENIS[jenis].tabel == tabel
    assert f"'{awalan}'" in ND.JENIS[jenis].generator or awalan == "PB"  # PB tertanam di fungsi generate_purchase_bill_number


def test_pratinjau_melompati_nomor_terpakai_seperti_generator():
    k = _K(last=29, terpakai={"INV-2611-0030", "INV-2611-0031", "INV-2610-0032", "NOTA-77"})
    assert asyncio.run(ND.pratinjau(k, "t", "sales_invoice")) == "INV-2611-0032"
    assert not _tulis(k)


def test_pratinjau_nomor_manual_tak_menggeser_seri_bila_tidak_bentrok():
    k = _K(last=29, terpakai={"NOTA-77", "INV-2611-0099"})
    assert asyncio.run(ND.pratinjau(k, "t", "sales_invoice")) == "INV-2611-0030"


def test_tangkap_unik_hanya_indeks_nomor():
    class UV(Exception):
        def __init__(self, constraint_name, detail):
            self.constraint_name, self.detail = constraint_name, detail
    ND.UniqueViolationError = UV  # tiruan; kelas asli diperiksa lewat isinstance di sumber
    e = ND.dari_pelanggaran_unik(UV("uq_sales_orders_number", "Key (tenant_id, order_number)=(t, SO-2611-0030) already exists."))
    assert e is not None and e.status_code == 409 and "SO-2611-0030" in e.detail["message"]
    assert ND.dari_pelanggaran_unik(UV("uq_idempotency_kunci", "Key (tenant_id, kunci)=(t, k) already exists.")) is None


def test_tangkap_unik_semua_indeks_terdaftar():
    assert {j.indeks for j in ND.JENIS.values()} == {
        "uq_sales_invoices_tenant_number", "uq_sales_orders_number", "uq_quotes_number", "uq_bills_tenant_invoice", "uq_po_tenant_number"}


def _sumber(modul):
    import importlib
    return inspect.getsource(importlib.import_module(f"app.{modul}"))


@pytest.mark.parametrize("modul,pakai", [
    ("routers.sales_invoices", "tetapkan_nomor("), ("routers.sales_orders", "tetapkan_nomor("), ("routers.quotes", "tetapkan_nomor("),
    ("routers.purchase_orders", "tetapkan_nomor("), ("services.bills_service", "tetapkan_nomor("),
    ("routers.bills", "dari_pelanggaran_unik(")])  # router tagihan memetakan balapan; nomor ditetapkan di bills_service
def test_penjaga_ast_satu_aturan_tak_ada_salinan(modul, pakai):
    s = _sumber(modul)
    assert pakai in s
    for lama in ("bersihkan_nomor_dokumen_opsional", "Nomor sudah dipakai di tenant ini.", "PESAN_PO_TERPAKAI",
                 "PESAN_NOMOR_TERPAKAI", "class NomorTerpakai", "def nomor_tagihan"):
        assert lama not in s, (modul, lama)
    assert not re.search(r'"SELECT 1 FROM (sales_invoices|sales_orders|quotes|purchase_orders|bills) WHERE tenant_id\s*=\s*\$1 AND (invoice_number|order_number|quote_number|po_number)', s), modul


@pytest.mark.parametrize("modul", ["routers.sales_invoices", "routers.sales_orders", "routers.quotes", "routers.purchase_orders", "routers.bills"])
def test_rute_pratinjau_nomor_ada_dan_sebelum_rute_id(modul):
    import importlib
    r = importlib.import_module(f"app.{modul}").router
    urut = [(x.path, tuple(x.methods)) for x in r.routes if "GET" in x.methods]
    idx = [i for i, (p, _) in enumerate(urut) if p.endswith("/next-number")]
    assert len(idx) == 1, modul
    pertama_id = min(i for i, (p, _) in enumerate(urut) if re.search(r"/\{\w+\}$", p))
    assert idx[0] < pertama_id, f"{modul}: /next-number harus sebelum rute /{{id}}"


def test_document_number_lama_dihapus():
    with pytest.raises(ImportError):
        from app.services import document_number  # noqa: F401
