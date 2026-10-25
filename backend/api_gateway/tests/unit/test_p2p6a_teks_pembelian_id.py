"""Item 6a Pembelian (25 Okt 2026, MASTER; kembar test_teks_penjualan_id): teks galat/pesan Inggris -> Indonesia ringkas tanpa kurung di
router/servis Pembelian. Status kode TIDAK berubah (scripts/ops/banding-status-6a.py LAMA vs BARU). Kopling teks: bills.py dulu memilih 404
dgn `result["message"] == "Bill not found"` -> konstanta bersama PESAN_TAGIHAN_TAK_ADA; tes AST menolak pencocokan teks baru."""
import ast
import pathlib
import re

from app.services import bills_service as BS
from app.services import teks_galat as tg

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
BERKAS = ["routers/bills.py", "routers/bill_payments.py", "routers/vendor_credits.py", "services/bills_service.py",
          "services/bayar_keluar_rencana.py", "services/nota_debit_rencana.py", "services/tagihan_aksi.py", "services/tagihan_rencana.py"]
EN = re.compile(r"\b(not found|failed to|cannot|can't|must be|only draft|only posted|is required|already|exceeds?|successfully|"
                r"no changes|deleted|voided|posted to|either|instead|invalid|created as|payment|bill|vendor credit)\b", re.I)


def _teks_pesan(pohon):
    """(baris, teks) dari HTTPException(detail=...), blok(kode, teks), X.galat(kode, status, teks), {"message": ...}."""
    for n in ast.walk(pohon):
        if isinstance(n, ast.Call):
            nama = getattr(n.func, "attr", getattr(n.func, "id", ""))
            cal = []
            if nama == "HTTPException":
                cal = [k.value for k in n.keywords if k.arg == "detail"]
            else:
                cal = [k.value for k in n.keywords if k.arg == "message"]  # Respons(message=...)
            if nama == "blok" and len(n.args) >= 2:
                cal = [n.args[1]]
            if nama == "galat" and len(n.args) >= 3:
                cal = [n.args[2]]
            for v in cal:
                if isinstance(v, (ast.Constant, ast.JoinedStr, ast.IfExp)):
                    yield n.lineno, _harfiah(v)
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == "message" and isinstance(v, (ast.Constant, ast.JoinedStr, ast.IfExp)):
                    yield n.lineno, _harfiah(v)


def _harfiah(v) -> str:
    """Bagian TEKS literal saja (isi {ekspresi} f-string dibuang) -- yang dibaca pengguna."""
    return " | ".join(c.value for c in ast.walk(v) if isinstance(c, ast.Constant) and isinstance(c.value, str))


def test_nol_teks_inggris_di_pembelian():
    sisa = [(f, b, t) for f in BERKAS for b, t in _teks_pesan(ast.parse((APP / f).read_text())) if EN.search(t)]
    assert sisa == [], sisa[:10]


def test_teks_baru_tanpa_kurung():
    """Teks UI ringkas tanpa '(...)' (aturan pemilik) -- untuk teks yang DISENTUH 6a; '(due_date)' dibuang."""
    for f in BERKAS:
        src = (APP / f).read_text()
        assert "(due_date)" not in src, f
    for t in ("Faktur pembelian tidak ditemukan.", "Nominal melebihi sisa faktur pembelian.", "Pembayaran draf tidak dibatalkan, hapus saja.",
              "Hanya nota debit berstatus Draf yang bisa dihapus. Nota debit terbit dibatalkan, bukan dihapus."):
        assert "(" not in t


def test_404_tagihan_pakai_konstanta_bersama():
    src = (APP / "routers" / "bills.py").read_text()
    assert 'status_code = 404 if result["message"] == PESAN_TAGIHAN_TAK_ADA else 400' in src
    assert BS.PESAN_TAGIHAN_TAK_ADA == "Faktur pembelian tidak ditemukan."
    assert '"Bill not found"' not in src and '"Bill not found"' not in (APP / "services" / "bills_service.py").read_text()


def _pencocokan_teks(pohon):
    """Compare yang membandingkan medan message/detail/error dgn LITERAL teks (kopling tersembunyi)."""
    for n in ast.walk(pohon):
        if isinstance(n, ast.Compare):
            sisi = [n.left, *n.comparators]
            ada_lit = any(isinstance(x, ast.Constant) and isinstance(x.value, str) and " " in x.value for x in sisi)
            ada_medan = any(re.search(r"message|detail|error", ast.unparse(x)) for x in sisi if not isinstance(x, ast.Constant))
            if ada_lit and ada_medan:
                yield n.lineno, ast.unparse(n)


def test_nol_pencocokan_teks_pesan_di_berkas_tersentuh():
    sisa = [(f, b, t) for f in BERKAS for b, t in _pencocokan_teks(ast.parse((APP / f).read_text()))]
    assert sisa == [], sisa


def test_kontrol_pemindai_bisa_gagal():
    contoh = ast.parse('if result["message"] == "Bill not found":\n    pass\nraise HTTPException(status_code=404, detail="Bill not found")')
    assert list(_pencocokan_teks(contoh)) and any(EN.search(t) for _, t in _teks_pesan(contoh))


def test_label_status_pembelian_kembar_fe():
    assert tg.tak_bisa_status("bill", "posted", "diposting") == "Faktur pembelian berstatus Belum lunas — tidak bisa diposting."
    assert tg.tak_bisa_status("vc", "posted", "diposting", "VC-2610-0001") == "Nota debit VC-2610-0001 berstatus Terbit — tidak bisa diposting."
    assert [tg.status_id("bill", s) for s in ("draft", "posted", "partial", "paid", "void")] == ["Draf", "Belum lunas", "Sebagian", "Lunas", "Batal"]
    assert [tg.status_id("bp", s) for s in ("draft", "posted", "void")] == ["Draf", "Dibayar", "Batal"]


def test_judul_galat_fe_tetap_cocok():
    """FE paymentErrorTitle mencocokkan 'exceeds remaining' ATAU 'melebihi' -> teks baru WAJIB memuat 'melebihi'."""
    src = (APP / "services" / "bayar_keluar_rencana.py").read_text()
    assert '"Nominal melebihi sisa faktur pembelian."' in src and "Amount exceeds remaining" not in src
