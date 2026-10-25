"""P2P-3a (25 Okt 2026, MASTER; simetri Penjualan): Nota Debit + Nota Kredit -- PATCH / DELETE / create-tax-invoice.
Urutan wajib: idem (mulai_aksi) PERTAMA di transaksi -> kunci advisory SAMA dengan posting -> FOR UPDATE -> If-Match + status di bawah kunci;
DELETE bersyarat status draf + tenant; create-tax-invoice: idem + kunci per sumber dipegang selama panggilan internal. Kolom hantu
credit_notes.tax_invoice_id tak lagi dibaca. Perilaku nyata (replay, badan beda 409, balapan hapus||posting) = harness uji-nyata-p2p3a."""
import ast
import inspect
from pathlib import Path

import pytest

from app.routers import credit_notes as CN
from app.routers import tax_invoices as TI
from app.routers import vendor_credits as VC

KASUS = [(VC, "update_vendor_credit", "VC_UPDATE", 'f"VENDOR_CREDIT:{vendor_credit_id}"'),
         (VC, "delete_vendor_credit", "VC_DELETE", 'f"VENDOR_CREDIT:{vendor_credit_id}"'),
         (CN, "update_credit_note", "CN_UPDATE", 'f"CREDIT_NOTE:{credit_note_id}"'),
         (CN, "delete_credit_note", "CN_DELETE", 'f"CREDIT_NOTE:{credit_note_id}"')]


def _badan(fn) -> str:
    src = inspect.getsource(fn)
    n = ast.parse(src).body[0]
    if isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
        src = src.replace(ast.get_source_segment(src, n.body[0]), "", 1)
    return src


@pytest.mark.parametrize("mod,nama,prefiks,kunci", KASUS)
def test_urutan_idem_kunci_forupdate_cek(mod, nama, prefiks, kunci):
    s = _badan(getattr(mod, nama))
    i_tx, i_idem = s.index("conn.transaction()"), s.index(f'"{prefiks}"')
    i_kunci, i_fu = s.index(kunci), s.index("FOR UPDATE")
    i_cek = s.index('["status"] != "draft"')
    assert i_tx < i_idem < i_kunci < i_fu < i_cek, nama
    assert "assert_if_match_row" not in s  # dulu di koneksi lain, di luar transaksi
    if nama.startswith("update"):
        assert i_fu < s.index("assert_if_match(request,") < i_cek
        assert "_jawab(" in s and s.count("return await _jawab(") == 2  # tanpa-perubahan + sukses tercatat untuk replay


@pytest.mark.parametrize("mod,nama,tabel", [(VC, "delete_vendor_credit", "vendor_credits"), (CN, "delete_credit_note", "credit_notes")])
def test_hapus_bersyarat_draf_dan_tenant(mod, nama, tabel):
    s = _badan(getattr(mod, nama))
    assert f"DELETE FROM {tabel} WHERE id = $1 AND tenant_id = $2 AND status = 'draft' RETURNING id" in s
    assert f'"DELETE FROM {tabel} WHERE id = $1"' not in s and "status_code=409" in s


@pytest.mark.parametrize("mod,nama,prefiks,kunci", [
    (VC, "create_tax_invoice_from_vc", "VC_TAX_INV", "TAX_INV_SRC:vendor_credit:"),
    (CN, "create_tax_invoice_from_cn", "CN_TAX_INV", "TAX_INV_SRC:credit_note:")])
def test_faktur_pajak_idem_dan_kunci_dipegang_selama_panggilan(mod, nama, prefiks, kunci):
    s = _badan(getattr(mod, nama))
    assert s.index(f'"{prefiks}"') < s.index(kunci) < s.index("http://localhost:8000/api/tax-invoices") < s.rindex("_ib.simpan(")
    # panggilan internal di DALAM blok transaksi (indentasi lebih dalam dari `async with pool.acquire() as conn, conn.transaction():`)
    baris_tx = next(l for l in s.splitlines() if "conn.transaction():" in l)
    baris_post = next(l for l in s.splitlines() if "await client.post(" in l)
    assert len(baris_post) - len(baris_post.lstrip()) > len(baris_tx) - len(baris_tx.lstrip())


def test_kolom_hantu_tax_invoice_id_tak_dibaca_dari_credit_notes():
    for src in (_badan(CN.create_tax_invoice_from_cn), Path(TI.__file__).read_text()):
        assert "tax_invoice_id FROM credit_notes" not in src and "total_amount, tax_invoice_id " not in src
    assert "tax_invoice_sources" in _badan(CN.create_tax_invoice_from_cn)


def test_kontrol_urutan_bisa_gagal():
    """Kontrol: pola urutan menangkap idem SESUDAH kunci (sabotase teks)."""
    s = _badan(VC.delete_vendor_credit)
    salah = s.replace('"VC_DELETE"', '"VC_X"', 1) + '\n# "VC_DELETE"'
    with pytest.raises(AssertionError):
        assert salah.index("conn.transaction()") < salah.index('"VC_DELETE"') < salah.index('f"VENDOR_CREDIT:{vendor_credit_id}"')
