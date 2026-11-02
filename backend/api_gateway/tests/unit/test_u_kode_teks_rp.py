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


def test_pola_tunggal_dp_dan_ump():
    from app.services import teks_galat as tg
    assert tg.penerapan_melebihi_sisa(Decimal("500"), Decimal("100.50")) == "Nilai penerapan Rp 500 melebihi sisa uang muka Rp 100,50."
    assert tg.penerapan_melebihi_sisa(Decimal("500"), Decimal("100"), "pelunasan") == "Nilai penerapan Rp 500 melebihi sisa pelunasan Rp 100."
    assert tg.pengembalian_melebihi_sisa(Decimal("1250000"), Decimal("50")) == "Pengembalian Rp 1.250.000 melebihi sisa uang muka Rp 50."
    assert tg.penerapan_melebihi_sisa_tagihan(Decimal("30000"), Decimal("20000.5")) == \
        "Nilai penerapan Rp 30.000 melebihi sisa tagihan faktur Rp 20.000,50."


def _panggil_tg(node, nama: str) -> bool:
    return isinstance(node, ast.Call) and ast.unparse(node.func) == f"tg.{nama}"


def test_penentu_ump_vendor_pola_tunggal():
    from app.services import uang_muka_vendor_rencana as UMVR
    for fn, kode, nama in ((UMVR.rencana_terapkan, "SALDO_TIDAK_CUKUP", "penerapan_melebihi_sisa"),
                           (UMVR.rencana_terapkan, "MELEBIHI_SISA_TAGIHAN", "penerapan_melebihi_sisa_tagihan"),
                           (UMVR.rencana_kembali, "SALDO_TIDAK_CUKUP", "pengembalian_melebihi_sisa")):
        arg = _argumen_kalimat(fn, kode)
        assert arg and all(_panggil_tg(a, nama) for a in arg), (fn.__name__, kode, [ast.unparse(a) for a in arg])


def test_penulis_ump_vendor_pola_tunggal():
    from app.routers import vendor_deposits as VD
    t = inspect.getsource(VD.terapkan_uang_muka_inti)
    r = inspect.getsource(VD.refund_vendor_deposit)
    assert "detail=tg.penerapan_melebihi_sisa(jumlah, sisa)" in t and "detail=tg.penerapan_melebihi_sisa_tagihan(jumlah, bill_remaining)" in t
    assert "detail=tg.pengembalian_melebihi_sisa(jumlah, sisa)" in r
    for src in (t, r):
        assert "Tersedia: " not in src and "Sisa tagihan: " not in src


def test_dp_penjualan_pola_tunggal_label_tetap():
    from app.services import uang_muka_rencana as UMR
    from app.routers import customer_deposits as CD
    src = inspect.getsource(UMR)
    assert "detail=tg.penerapan_melebihi_sisa(total, remaining, (await label_deposit(conn, tid, deposit_id)).lower())" in src
    assert "detail=tg.pengembalian_melebihi_sisa(body.amount, remaining, (await label_deposit(conn, tid, deposit_id)).lower())" in src
    assert "detail=tg.penerapan_melebihi_sisa_tagihan(app.amount, sisa)" in src
    assert '"Nilai penerapan melebihi sisa tagihan faktur."' not in src
    c = inspect.getsource(CD)
    assert "tg.pengembalian_melebihi_sisa(body.amount, sisa, (await label_deposit(conn, tid, deposit_id)).lower())" in c
    assert "tg.penerapan_melebihi_sisa(total, sisa, (await label_deposit(conn, tid, deposit_id)).lower())" in c
    assert 'f"Nilai penerapan {' not in c and 'f"Pengembalian {_rp_dp' not in c
