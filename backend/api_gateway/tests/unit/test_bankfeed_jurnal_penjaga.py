"""Penjaga tabel jurnal untuk SEMUA modul app/services/bankfeed_*.py (BF-1e; putusan MASTER 13 Nov): jurnal hanya BOLEH DIBACA lewat SQL bernama yang terdaftar di
IZIN_BACA_JURNAL (modul, nama konstanta). SQL jurnal kedua di tempat lain -> merah; tulis/hapus jurnal (INSERT/UPDATE/DELETE) tak pernah boleh di modul bankfeed.
Pengecualian TERBUKA di sini, bukan lewat memindah SQL ke modul lain: SQL_TERTAUT (bankfeed_dokumen) = tautan->dokumen daftar; aksi/tebak = pendahulu c3/b1b yang sudah LIVE."""
import ast
import glob
import os
import re

TABEL_JURNAL = re.compile(r"\bjournal_entries\b|\bjournal_lines\b|\bjournals\b", re.I)
TULIS = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", re.I)
IZIN_BACA_JURNAL = {("bankfeed_dokumen", "SQL_TERTAUT"), ("bankfeed_aksi", "SQL_PEMBALIK"), ("bankfeed_aksi", "SQL_DOK_TAUTAN"),
                    ("bankfeed_aksi", "SQL_JURNAL_BARIS"), ("bankfeed_tebak", "_KANDIDAT_DARI")}
DIR = os.path.join(os.path.dirname(__file__), "..", "..", "app", "services")


def temuan(sumber: str, modul: str) -> tuple:
    """(tempat terlarang, tulis) — tempat = string berisi tabel jurnal di luar konstanta modul yang terdaftar."""
    pohon, aman, liar, tulis = ast.parse(sumber), set(), [], []
    for n in pohon.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and (modul, n.targets[0].id) in IZIN_BACA_JURNAL:
            aman.update(id(x) for x in ast.walk(n))
    for n in pohon.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            teks = " ".join(s.value for s in ast.walk(n) if isinstance(s, ast.Constant) and isinstance(s.value, str))
            if TABEL_JURNAL.search(teks) and TULIS.search(teks):
                tulis.append(n.targets[0].id)
    for s in ast.walk(pohon):
        if isinstance(s, ast.Constant) and isinstance(s.value, str) and TABEL_JURNAL.search(s.value) and len(s.value.split()) > 1 and id(s) not in aman:   # kunci dict satu kata ('journals') bukan SQL
            liar.append((s.lineno, s.value[:60]))
    return liar, tulis


def _modul():
    for f in sorted(glob.glob(os.path.join(DIR, "bankfeed_*.py"))):
        yield os.path.basename(f)[:-3], open(f).read()


def test_tabel_jurnal_hanya_di_sql_baca_bernama_yang_terdaftar():
    for modul, src in _modul():
        liar, tulis = temuan(src, modul)
        assert not liar, f"{modul}: SQL jurnal di luar IZIN_BACA_JURNAL {liar}"
        assert not tulis, f"{modul}: tulis/hapus jurnal terlarang {tulis}"


def test_sql_baca_jurnal_terdaftar_semuanya_select_dan_ada_di_modulnya():
    from importlib import import_module
    for modul, nama in IZIN_BACA_JURNAL:
        nilai = getattr(import_module(f"app.services.{modul}"), nama)
        assert nilai.lstrip().upper().startswith(("SELECT", "FROM")) and not TULIS.search(nilai) and "tenant_id" in nilai, (modul, nama)


def test_penjaga_menolak_sql_jurnal_kedua_dan_tulis_jurnal():
    kedua = 'X = 1\nSQL_JURNAL_KEDUA = "SELECT 1 FROM journal_entries WHERE tenant_id = $1"\n'
    assert temuan(kedua, "bankfeed_daftar")[0]
    assert temuan(kedua, "bankfeed_dokumen")[0]                                                  # nama lain di modul yang diizinkan pun ditolak
    assert temuan('SQL_TERTAUT = "SELECT 1 FROM journal_entries"\n', "bankfeed_daftar")[0]       # nama diizinkan di modul LAIN ditolak
    assert not temuan('SQL_TERTAUT = "SELECT 1 FROM journal_entries"\n', "bankfeed_dokumen")[0]
    assert temuan('SQL_X = "DELETE FROM journal_lines WHERE tenant_id = $1"\n', "bankfeed_dokumen")[1]
    assert temuan('async def f(c):\n    await c.fetch("SELECT * FROM journal_entries")\n', "bankfeed_dokumen")[0]  # SQL inline dalam fungsi ditolak
