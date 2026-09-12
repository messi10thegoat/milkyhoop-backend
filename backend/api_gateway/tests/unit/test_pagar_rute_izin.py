"""PENJAGA STRUKTURAL — tiap rute TULIS wajib punya pola izin, atau tercatat.

KENAPA ADA: celah izin tumbuh TANPA GAGAL. Endpoint ditambah, pola tidak
ditambah, dan tak ada yang merah — tak ada galat, tak ada tes jatuh, tak ada
peringatan. Begitulah 329 rute tulis berakhir tanpa penjaga (sapuan 12 Sep 2026,
`backend/docs/TEMUAN-sapuan-izin-20260912.md`). Penjaga ini mengubah "tumbuh
diam-diam" jadi "merah saat ditambahkan".

RANCANGAN YANG DITOLAK, dan sebabnya — supaya tak ada yang "menyederhanakannya"
kembali ke sana: versi pertama membaca rute dengan PARSE STATIS berkas router.
Diukur 12 Sep: parse statis melewatkan **14 rute hidup** (termasuk SELURUH
keluarga `/api/payroll/{run_id}/post|void|approve`) dan mengarang **45** yang
tak ada, karena prefiks `include_router` dan nama parameter dibaca keliru.
Penjaga yang buta terhadap rute penulis-jurnal akan HIJAU sambil melewatkan
persis kelas yang ia diciptakan untuk menangkap.

RANCANGAN YANG DIPAKAI: inventaris rute dihasilkan dari **tabel rute aplikasi
yang berjalan** (`app.routes`) lalu DI-COMMIT, dan uji unit ini membacanya.
Suite unit menyatakan dirinya "nol HTTP/LLM/DB" (`pytest-unit.ini`), jadi ia
tidak boleh mengimpor aplikasi — membaca berkas hasil adalah cara memenuhi
kedua syarat sekaligus.

BATAS YANG DIKETAHUI: inventaris bisa BASI. Itu ditangani oleh gerbang
kontainer `scripts/gate_izin_rute.py`, yang membandingkan inventaris terhadap
`app.routes` yang sesungguhnya dan MERAH kalau berbeda. Pembagiannya disengaja:
**inventaris basi gagal BERISIK; parser buta gagal DIAM-DIAM.**
"""
import re
from pathlib import Path

DISINI = Path(__file__).parent
AKAR = DISINI.parents[1]  # backend/api_gateway
MIDDLEWARE = AKAR / "app" / "middleware" / "permission_middleware.py"
INVENTARIS = DISINI / "inventaris_rute_tulis.txt"
GARIS_DASAR = DISINI / "garis_dasar_rute_tanpa_pola.txt"


def _baris(p: Path):
    if not p.exists():
        return []
    return [
        b.strip()
        for b in p.read_text(encoding="utf-8").splitlines()
        if b.strip() and not b.lstrip().startswith("#")
    ]


def _pola():
    teks = MIDDLEWARE.read_text(encoding="utf-8")
    blok = re.search(r"ROUTE_PERMISSIONS[^=]*=\s*\[(.*?)\n\]", teks, re.S).group(1)
    mentah = re.findall(
        r'\(r"([^"]+)",\s*\[([^\]]*)\],\s*"([^"]+)",\s*"([^"]+)"\)', blok
    )
    return [
        (re.compile(p), [x.strip().strip('"').strip("'") for x in m.split(",")])
        for p, m, _, _ in mentah
    ]


def _skip():
    teks = MIDDLEWARE.read_text(encoding="utf-8")
    blok = re.search(r"SKIP_PATTERNS\s*=\s*\[(.*?)\n\]", teks, re.S).group(1)
    return [re.compile(p) for p in re.findall(r"r[\"']([^\"']+)[\"']", blok)]


def _konkret(jalur: str) -> str:
    return re.sub(r"\{[^}]+\}", "XX", jalur)


def _tanpa_pola():
    pola, skip = _pola(), _skip()
    kurang = set()
    for baris in _baris(INVENTARIS):
        metode, _, jalur = baris.partition(" ")
        if not jalur or any(s.match(jalur) for s in skip):
            continue
        k = _konkret(jalur)
        if any(rx.match(k) and metode in mth for rx, mth in pola):
            continue
        kurang.add(baris)
    return kurang


def test_tak_ada_rute_tulis_baru_tanpa_pola_izin():
    """Rute tulis BARU tanpa pola = MERAH. Yang lama tercatat di garis dasar."""
    baru = sorted(_tanpa_pola() - set(_baris(GARIS_DASAR)))
    assert not baru, (
        "Rute TULIS tanpa pola izin di ROUTE_PERMISSIONS:\n  "
        + "\n  ".join(baru)
        + "\n\nTambahkan polanya di app/middleware/permission_middleware.py.\n"
        "Kalau rute ini memang SENGAJA terbuka, daftarkan di\n"
        "tests/unit/garis_dasar_rute_tanpa_pola.txt beserta sebab tertulis.\n"
        "Jangan menghapus tes ini."
    )


def test_garis_dasar_hanya_menyusut():
    """Entri garis dasar yang sudah tertutup WAJIB dicabut.

    Tanpa ini, garis dasar membeku jadi spesifikasi dan penjaga di atas perlahan
    berhenti menjaga apa pun — kelas 'gerbang yang membekukan cacat jadi spec'.
    """
    basi = sorted(set(_baris(GARIS_DASAR)) - _tanpa_pola())
    assert not basi, (
        "Entri garis dasar ini polanya SUDAH ditutup — cabut dari berkas:\n  "
        + "\n  ".join(basi)
    )


def test_inventaris_tidak_kosong():
    """Kontrol positif: inventaris kosong membuat kedua tes di atas HIJAU selamanya."""
    n = len(_baris(INVENTARIS))
    assert n > 300, (
        "Inventaris hanya memuat %d rute; ambang wajar >300. "
        "Kemungkinan berkasnya kosong/rusak, dan penjaga ini berhenti mengukur "
        "apa pun sambil tetap hijau." % n
    )


def test_pola_terbaca_dalam_jumlah_wajar():
    """Kontrol positif kedua: nol pola terbaca = semua rute tampak telanjang."""
    n = len(_pola())
    assert n > 200, (
        "Hanya %d pola terbaca dari permission_middleware.py; ambang wajar >200. "
        "Kemungkinan bentuk daftar berubah dan pembacanya berhenti melihat." % n
    )
