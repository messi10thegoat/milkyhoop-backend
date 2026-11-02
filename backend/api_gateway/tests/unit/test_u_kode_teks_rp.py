"""U-kode (2 Nov): kalimat 'melebihi sisa' ND + bayar-tagihan memakai tg.rp (cermin NK/SI). Kode + status TIDAK berubah.
Dulu: ND `{:,.0f}` membulatkan sen (Rp 500,50 -> 501) tanpa 'Rp'; bayar-tagihan `{:,}` = koma Inggris '1,000,000' (Law 9)."""
import ast
import inspect
from decimal import Decimal

from app.services import bayar_keluar_rencana as BKR
from app.services import nota_debit_rencana as NDR


def test_nd_melebihi_sisa_tagihan_rupiah_bersen():
    assert NDR.kalimat_nd_melebihi_sisa_tagihan(Decimal("500.50"), Decimal("1000"), "PB-2611-0001") == \
        "Melebihi sisa tagihan Rp 500,50: nota debit Rp 1.000 atas PB-2611-0001."
    assert NDR.kalimat_nd_melebihi_sisa_tagihan(Decimal("0"), Decimal("1250000"), None) == \
        "Melebihi sisa tagihan Rp 0: nota debit Rp 1.250.000 atas tagihan."


def test_nd_refund_melebihi_sisa_rupiah_bersen():
    assert NDR.kalimat_nd_refund_melebihi_sisa(Decimal("1250000"), Decimal("1000000.5")) == \
        "Pengembalian dana Rp 1.250.000 melebihi sisa nota debit Rp 1.000.000,50."


def test_bayar_tagihan_melebihi_sisa_rupiah_titik():
    assert BKR.kalimat_bayar_melebihi_sisa(Decimal("1000000"), 900000) == \
        "Jumlah pembayaran Rp 1.000.000 melebihi sisa tagihan Rp 900.000."


def _argumen_kalimat(fn, kode: str) -> list:
    """Argumen ke-2 (kalimat) tiap panggilan bl.galat(kode, ...) / blok(kode, ...) di sumber fungsi."""
    pohon = ast.parse(inspect.getsource(fn).lstrip())
    hasil = []
    for n in ast.walk(pohon):
        if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == kode:
            nama = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            if nama in ("galat", "blok"):
                hasil.append(n.args[-1])
    return hasil


def _panggil(node, nama: str) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", None) == nama


def test_penentu_memakai_kalimat_bersama():
    # ND posting + terapkan: satu kalimat; ND refund; bayar-tagihan. Semua jalur kode itu WAJIB lewat fungsi kalimat.
    cek = [(NDR.rencana_posting, "MELEBIHI_SISA_TAGIHAN", "kalimat_nd_melebihi_sisa_tagihan"),
           (NDR.rencana_terapkan, "MELEBIHI_SISA_TAGIHAN", "kalimat_nd_melebihi_sisa_tagihan"),
           (NDR.rencana_kembali, "MELEBIHI_SISA", "kalimat_nd_refund_melebihi_sisa"),
           (BKR.rencana_bayar_tagihan, "MELEBIHI_SISA_TAGIHAN", "kalimat_bayar_melebihi_sisa")]
    for fn, kode, kalimat in cek:
        arg = _argumen_kalimat(fn, kode)
        assert arg, (fn.__name__, kode)
        assert all(_panggil(a, kalimat) for a in arg), (fn.__name__, [ast.unparse(a) for a in arg])


def test_tanpa_format_angka_mentah_di_kalimat_uang():
    for mod in (NDR, BKR):
        src = inspect.getsource(mod)
        assert ":,.0f}" not in src and ":,}" not in src, mod.__name__


def test_ump_vendor_melebihi_sisa_tagihan_rupiah():
    from app.services import uang_muka_vendor_rencana as UMVR
    assert UMVR.kalimat_melebihi_sisa_tagihan(Decimal("274.50")) == "Jumlah melebihi sisa tagihan. Sisa tagihan: Rp 274,50."
    arg = _argumen_kalimat(UMVR.rencana_terapkan, "MELEBIHI_SISA_TAGIHAN")
    assert arg and all(_panggil(a, "kalimat_melebihi_sisa_tagihan") for a in arg)


def test_penulis_apply_uang_muka_vendor_kalimat_sama():
    # cek ganda penulis /apply (sesudah penentu) WAJIB kalimat yang sama, bukan Decimal mentah
    from app.routers import vendor_deposits as VD
    src = inspect.getsource(VD.terapkan_uang_muka_inti)
    assert "detail=R.kalimat_melebihi_sisa_tagihan(bill_remaining)" in src
    assert "Sisa tagihan: {" not in src


def test_ump_vendor_saldo_tidak_cukup_rupiah_sama_dgn_dp_penjualan():
    from app.services import uang_muka_vendor_rencana as UMVR
    from app.services import teks_galat as tg
    assert UMVR.kalimat_saldo_tidak_cukup(Decimal("100")) == "Saldo uang muka tidak cukup. Tersedia: Rp 100."
    assert UMVR.kalimat_saldo_tidak_cukup(Decimal("1250.5")) == f"Saldo uang muka tidak cukup. Tersedia: {tg.rp(Decimal('1250.5'))}."
    for fn in (UMVR.rencana_terapkan, UMVR.rencana_kembali):
        arg = _argumen_kalimat(fn, "SALDO_TIDAK_CUKUP")
        assert arg and all(_panggil(a, "kalimat_saldo_tidak_cukup") for a in arg), fn.__name__


def test_penulis_uang_muka_vendor_saldo_kalimat_sama():
    from app.routers import vendor_deposits as VD
    for fn in (VD.terapkan_uang_muka_inti, VD.refund_vendor_deposit):
        src = inspect.getsource(fn)
        assert "detail=R.kalimat_saldo_tidak_cukup(sisa)" in src and "Tersedia: {" not in src, fn.__name__
