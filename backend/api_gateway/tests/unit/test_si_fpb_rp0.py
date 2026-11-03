"""SI-FPB-RP0 (3 Nov 2026, putusan pemilik LANGSUNG, OK MASTER): (1) posting faktur PENJUALAN dan PEMBELIAN bernilai Rp 0 DITOLAK
berkalimat 400 dari SATU fungsi (services/dokumen_nol) -- pratinjau memblok dengan kalimat yang SAMA; draf Rp 0 tetap boleh;
(2) baris jurnal tagihan SI bernilai Rp 0 dilewati -> faktur hanya ongkir/pajak terbit seimbang (dulu jl_has_amount -> 500).
Kesetaraan penulis untuk faktur/tagihan normal = SETARA perekam; tes ini mengunci aturan + bahwa SEMUA pintu memanggil fungsi yang sama."""
import ast
import inspect
from decimal import Decimal as D

import pytest

from app.routers import bills as RB
from app.routers import sales_invoices as SI
from app.services import bills_service as BS
from app.services import dokumen_nol as DN
from app.services import faktur_terbit_rencana as F
from app.services import tagihan_aksi as TA
from app.services import teks_galat as tg
from app.services.galat_berkode import GalatBerkode

KAL_SI = "Faktur penjualan bernilai Rp 0 tidak bisa diterbitkan. Isi harga atau hapus diskon."
KAL_FPB = "Faktur pembelian bernilai Rp 0 tidak bisa diterbitkan. Isi harga atau hapus diskon."


def test_kalimat_persis():
    assert tg.faktur_nol("si") == KAL_SI and tg.faktur_nol("bill") == KAL_FPB


@pytest.mark.parametrize("jenis,kal,field", [("si", KAL_SI, "total_amount"), ("bill", KAL_FPB, "grand_total")])
@pytest.mark.parametrize("total", [0, "0.00", D("-1"), None])
def test_tolak_total_nol_400_berkode(jenis, kal, field, total):
    with pytest.raises(GalatBerkode) as e:
        DN.tolak_faktur_nol(jenis, total, field)
    assert e.value.status_code == 400 and e.value.badan() == {"detail": kal, "code": "FAKTUR_NOL", "message": kal, "field": field}


@pytest.mark.parametrize("jenis", ["si", "bill"])
@pytest.mark.parametrize("total,ditolak", [(0, True), (D("-0.01"), True), (D("0.01"), False), (1000, False)])
def test_pratinjau_blok_sama_dengan_tulis(jenis, total, ditolak):
    """Blok pratinjau ⇔ penulis 400, kalimat SAMA (fungsi tolak yang sama)."""
    blok = DN.blok_faktur_nol(jenis, total)
    try:
        DN.tolak_faktur_nol(jenis, total)
        tulis = None
    except GalatBerkode as e:
        tulis = e.detail
    assert bool(blok) is ditolak and (tulis is not None) is ditolak
    if ditolak:
        assert blok == [{"code": "FAKTUR_NOL", "message": tulis}]


def test_baris_jv_lewati_rp0_hanya_ongkir_seimbang():
    b = F.baris_jv(D("5000"), D("0"), D("5000"), "AR", "DM", "ONG", "PPN", "INV-9")
    assert [(x["akun"], x["debit"], x["kredit"]) for x in b] == [("AR", D("5000"), 0), ("ONG", 0, D("5000"))]
    p = F.baris_jv(D("610"), D("110"), D("500"), "AR", "DM", "ONG", "PPN", "INV-8")  # ongkir + PPN, barang 0
    assert [x["akun"] for x in p] == ["AR", "ONG", "PPN"] and sum(x["debit"] for x in p) == sum(x["kredit"] for x in p) == D("610")
    assert all(x["debit"] != 0 or x["kredit"] != 0 for x in b + p)


def test_baris_jv_faktur_normal_tak_berubah():
    b = F.baris_jv(D("1137000"), D("110000"), D("27000"), "AR", "DM", "ONG", "PPN", "INV-1")
    assert [(x["akun"], x["debit"], x["kredit"], x["sisi"]) for x in b] == [
        ("AR", D("1137000"), 0, "D"), ("DM", 0, D("1000000"), "K"), ("ONG", 0, D("27000"), "K"), ("PPN", 0, D("110000"), "K")]


def _panggilan(fn):
    """[(nama_fungsi, argumen_pertama_konstanta)] semua Call di fungsi (AST, docstring tak ikut)."""
    out = []
    for n in ast.walk(ast.parse(inspect.getsource(fn).lstrip() if not inspect.getsource(fn).startswith(" ") else
                                "if 1:\n" + inspect.getsource(fn))):
        if isinstance(n, ast.Call):
            nama = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            a0 = n.args[0].value if n.args and isinstance(n.args[0], ast.Constant) else None
            out.append((nama, a0))
    return out


@pytest.mark.parametrize("fn,nama,jenis", [
    (SI._internal_post_invoice, "tolak_faktur_nol", "si"),       # penulis SI (create auto_post, POST /post, SO->faktur)
    (SI.calculate_invoice, "tolak_faktur_nol", "si"),            # pratinjau buat SI (auto_post)
    (F.rencana_terbit_ro, "blok_faktur_nol", "si"),              # pratinjau terbit SI
    (BS.BillsService.create_bill_v2, "tolak_faktur_nol", "bill"),  # penulis FPB buat-langsung-posting
    (TA.blok_posting, "blok_faktur_nol", "bill"),                # penulis + pratinjau posting draf FPB
    (RB.calculate_bill_totals, "blok_faktur_nol", "bill"),       # pratinjau buat FPB
])
def test_semua_pintu_memanggil_fungsi_yang_sama(fn, nama, jenis):
    assert (nama, jenis) in _panggilan(fn), (fn.__name__, nama, jenis)


def test_simetri_si_dan_fpb_keduanya_menolak():
    """Sabotase 'tolak dimatikan untuk FPB saja' harus merah: kedua modul punya penulis DAN pratinjau yang memanggil dokumen_nol."""
    pintu = {"si": [SI._internal_post_invoice, SI.calculate_invoice, F.rencana_terbit_ro],
             "bill": [BS.BillsService.create_bill_v2, TA.blok_posting, RB.calculate_bill_totals]}
    for jenis, fns in pintu.items():
        ada = [any(p[0] in ("tolak_faktur_nol", "blok_faktur_nol") and p[1] == jenis for p in _panggilan(f)) for f in fns]
        assert all(ada), (jenis, ada)


def test_penulis_si_tolak_sebelum_tulis():
    """_internal_post_invoice: tolak_faktur_nol dipanggil SEBELUM INSERT pertama (accounts_receivable)."""
    src = inspect.getsource(SI._internal_post_invoice)
    assert 0 < src.index('tolak_faktur_nol("si"') < src.index("INSERT INTO accounts_receivable")


def test_penulis_fpb_tolak_sebelum_tulis_tagihan():
    src = inspect.getsource(BS.BillsService.create_bill_v2)
    i = src.index('DN.tolak_faktur_nol("bill"')
    assert i < src.index("INSERT INTO bills") and src.rindex("TR.hitung", 0, i) >= 0


def test_kalimat_hanya_dari_satu_fungsi():
    """Tak ada konstanta kalimat tolak Rp 0 tulisan tangan di luar teks_galat (AST: konstanta string, node docstring dikecualikan)."""
    import pathlib
    akar = pathlib.Path(DN.__file__).resolve().parents[1]
    liar = []
    for f in akar.rglob("*.py"):
        pohon = ast.parse(f.read_text())
        doc = {id(n.body[0].value) for n in ast.walk(pohon) if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(pohon):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc and "Isi harga atau hapus diskon" in n.value:
                liar.append(f"{f.relative_to(akar)}:{n.lineno}")
    assert liar and all(x.startswith("services/teks_galat.py:") for x in liar), liar


def test_saldo_awal_vendor_nol_tak_sampai_post_bill():
    """Syarat MASTER: saldo awal vendor 0 tak pernah memanggil post_bill (tolak lama 'Amount must be greater than zero' lebih dulu);
    buat vendor dengan opening_balance 0 tak menyentuh posting sama sekali."""
    from app.routers import vendors as RV
    src = inspect.getsource(RV.set_vendor_opening_balance)
    assert 0 < src.index("if masuk.amount <= 0:") < src.index("svc.post_bill(")
    assert "post_bill" not in inspect.getsource(RV.create_vendor)


def test_tagihan_berulang_gagal_posting_tetap_draf_tanpa_raise():
    """Syarat MASTER: tagihan berulang Rp 0 -> post_bill success False -> status tetap 'draft' untuk item itu (tanpa raise);
    batch /process-due tidak memanggil post_bill."""
    from app.routers import recurring_bills as RRB
    src = inspect.getsource(RRB.generate_bill)
    i = src.index(".post_bill(")
    assert 'if hasil.get("success"):' in src[i:i + 300] and "raise" not in src[i:i + 300]
    assert "post_bill" not in inspect.getsource(RRB.process_due_bills)
