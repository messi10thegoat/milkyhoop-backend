"""U-SI-TERBIT B1 (2 Nov 2026, disetujui MASTER): pratinjau TERBIT faktur penjualan READ ONLY dari penentu YANG SAMA dengan penulis
_internal_post_invoice (services/faktur_terbit_rencana). Kesetaraan penulis lama vs baru dibuktikan SETARA perekam di PG sekali-pakai;
tes ini mengunci isi fungsi murni + bahwa penulis & pratinjau memanggilnya + pratinjau tanpa kunci/tulis/penomoran."""
import ast
import inspect
from decimal import Decimal as D

from app.routers import sales_invoices as SI
from app.services import faktur_terbit_rencana as F
from app.services import uang_muka_rencana as UMR


def test_baris_jv_dimuka_selalu_ongkir_ppn_bila_ada():
    b = F.baris_jv(D("1137000"), D("110000"), D("27000"), "AR", "DM", "ONG", "PPN", "INV-1")
    assert [(x["akun"], x["debit"], x["kredit"], x["sisi"]) for x in b] == [
        ("AR", D("1137000"), 0, "D"), ("DM", 0, D("1000000"), "K"), ("ONG", 0, D("27000"), "K"), ("PPN", 0, D("110000"), "K")]
    assert [x["memo"] for x in b] == ["Piutang - INV-1", "Pendapatan Diterima Dimuka - INV-1", "Pendapatan Ongkos Kirim - INV-1",
                                      "PPN Keluaran - INV-1"]
    t = F.baris_jv(D("0"), 0, 0, "AR", "DM", None, None, "X")
    assert [x["akun"] for x in t] == ["AR", "DM"] and t[1]["kredit"] == 0       # Dimuka SELALU, walau 0
    assert sum(x["debit"] for x in b) == sum(x["kredit"] for x in b)              # Law 4


def test_alokasi_baris_proporsi_half_up_sisa_ke_baris_terakhir():
    items = [{"quantity": 1, "unit_price": D("100000"), "discount_amount": 0, "jumlah_baris": None},
             {"quantity": 1, "unit_price": D("33333"), "discount_amount": 0, "jumlah_baris": None},
             {"quantity": 3, "unit_price": D("1"), "discount_amount": D("1"), "jumlah_baris": None}]
    a = F.alokasi_baris(items, D("133335"), 0, 0)
    assert sum(a) == D("133335") and a[0] == D("100000.00") and a[1] == D("33333.00") and a[2] == D("2")
    v = F.alokasi_baris([{"quantity": 2, "unit_price": 1, "jumlah_baris": D("5"), "discount_amount": 0},
                         {"quantity": 1, "unit_price": D("10"), "jumlah_baris": None, "discount_amount": 0}], D("15.01"), D("0.01"), 0)
    assert v == [D("5.00"), D("10.00")]                                            # V427 jumlah_baris dipakai
    assert F.alokasi_baris([{"quantity": 1, "unit_price": 0, "jumlah_baris": None, "discount_amount": 0}], D("10"), 0, 0) == [D("0")]


def test_pilih_cabang_urutan_penulis():
    assert F.pilih_cabang(D("1"), True, True, False, "invoice") == F.CABANG_SJ_PENUH
    assert F.pilih_cabang(0, True, True, True, "delivery") == F.CABANG_TANGGUH_KIRIM
    assert F.pilih_cabang(0, True, True, True, "invoice") == F.CABANG_KIRIM_OTOMATIS
    assert F.pilih_cabang(0, True, False, False, "invoice") == F.CABANG_TANGGUH_WAC0
    assert F.pilih_cabang(0, False, True, False, "delivery") == F.CABANG_JASA


def test_pecahan_dan_recog():
    assert F.pecah_pendapatan([(D("100"), "service"), (D("50"), "goods"), (D("-5"), "service"), (D("7"), None)]) == (D("57"), D("95"))  # U-SI-LAW (putusan pemilik): -5 masuk ember jasa, Σ kredit == Σ alokasi
    assert F.jumlah_recog_jasa([D("100"), D("-5")]) == D("95")                     # perilaku lama dipertahankan (tiket terpisah)
    r = F.baris_recog(D("150"), D("50"), D("100"), "DM", "Dimuka", "INV-9")
    assert [(x["akun"], x["debit"], x["kredit"], x["jasa"]) for x in r] == [("DM", D("150"), 0, None), (None, 0, D("50"), False),
                                                                             (None, 0, D("100"), True)]
    assert len(F.baris_recog(D("10"), D("10"), 0, "DM", "m", "n")) == 2


def _panggil(fn, prefiks="FTR."):
    pohon = ast.parse(inspect.getsource(fn).lstrip())
    return [ast.unparse(n.func) for n in ast.walk(pohon) if isinstance(n, ast.Call) and ast.unparse(n.func).startswith(prefiks)]


def test_penulis_memakai_penentu_bersama():
    p = _panggil(SI._internal_post_invoice)
    for f in ("FTR.baris_jv", "FTR.alokasi_baris", "FTR.pilih_cabang", "FTR.jumlah_recog_jasa"):
        assert p.count(f) == 1, f
    assert p.count("FTR.pecah_pendapatan") == 2 and p.count("FTR.baris_recog") == 2
    s = inspect.getsource(SI._internal_post_invoice)
    assert "proportion * subtotal_after_discount" not in s and "_svc_rev += _a" not in s  # tak ada salinan hitungan inline


def test_pratinjau_baca_saja_tanpa_kunci_penulis_penomoran():
    # PANGGILAN + konstanta SQL (bukan teks/docstring -- pelajaran spek kata vs panggilan)
    pohon = ast.parse(inspect.getsource(F.rencana_terbit_ro).lstrip())
    panggil = {ast.unparse(n.func).split(".")[-1] for n in ast.walk(pohon) if isinstance(n, ast.Call)}
    for terlarang in ("terbitkan_faktur", "_internal_post_invoice", "apply_deposit_core", "_execute_fulfillment",
                      "alokasikan_saat_faktur", "get_next_journal_number", "execute"):
        assert terlarang not in panggil, terlarang
    fn = pohon.body[0]
    doc = fn.body[0].value if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else None
    sql = [n.value.upper() for n in ast.walk(pohon) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc]
    sql += [F.SQL_ITEMS.upper(), F.SQL_SJ_MUNGKIN.upper()]
    for frasa in ("INSERT ", "UPDATE ", "DELETE ", "FOR UPDATE", "PG_ADVISORY", "GET_NEXT_JOURNAL_NUMBER"):
        assert not any(frasa in q for q in sql), frasa
    for wajib in ("baris_jv", "alokasi_baris", "pilih_cabang", "pecah_pendapatan", "jumlah_recog_jasa", "baris_recog",
                  "baris_terapkan", "plan_so_deposit_application", "tanggal_terap_otomatis"):
        assert wajib in panggil, wajib
    src = inspect.getsource(F.rencana_terbit_ro)
    assert "if cabang == CABANG_KIRIM_OTOMATIS:\n        complete = False" in src and "complete = not sj_mungkin" in src


def test_rute_pratinjau_readonly_rollback_bentuk():
    s = inspect.getsource(SI.preview_post_invoice)
    assert s.index("transaction(readonly=True)") < s.index("rencana_terbit_ro(") < s.index("tr.rollback()")
    for k in ('"action": "post"', '"complete": r["complete"]', '"payload": None', '"preview": True', '"deposit_application_journals"'):
        assert k in s, k


def test_baris_terapkan_satu_penentu_penulis_dp():
    assert UMR.baris_terapkan("DP", "AR", D("200"), "Uang Muka", "INV-1", "DP-1") == [
        ("DP", D("200"), 0, "Aplikasi Uang Muka - INV-1"), ("AR", 0, D("200"), "Pelunasan dari Deposit - DP-1")]
    assert "baris_terapkan(akun_dp, akun_ar, app.amount, label_k" in inspect.getsource(UMR.rencana_terapkan)


def test_penulis_alokasi_hanya_dari_penentu():
    # S5 (hanya tertangkap SETARA) -> tes unit (aturan MASTER): alokasi penulis = keluaran alokasi_baris APA ADANYA
    s = inspect.getsource(SI._internal_post_invoice)
    assert '_alokasi = FTR.alokasi_baris(items, invoice["total_amount"], invoice.get("tax_amount", 0) or 0, invoice.get("shipping_amount") or 0)' in s
    assert "    items = [dict(itm, _allocated=_a) for itm, _a in zip(items, _alokasi)]\n" in s
    pohon = ast.parse(s.lstrip())
    tugas_kw = [n for n in ast.walk(pohon) if isinstance(n, ast.keyword) and n.arg == "_allocated"]
    tugas_sub = [n for n in ast.walk(pohon) if isinstance(n, (ast.Assign, ast.AugAssign))
                 and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "_allocated"
                         for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    assert len(tugas_kw) == 1 and not tugas_sub


def test_alokasi_sepertiga_sisa_ke_baris_terakhir():
    items = [{"quantity": 1, "unit_price": D("10000"), "discount_amount": 0, "jumlah_baris": None} for _ in range(3)]
    a = F.alokasi_baris(items, D("29999"), 0, 0)
    assert a == [D("9999.67"), D("9999.67"), D("9999.66")] and sum(a) == D("29999")
