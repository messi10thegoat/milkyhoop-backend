"""Kontrak PATCH Penawaran: absen / null / "" harus DIBEDAKAN.

MASALAH YANG DIPERBAIKI
`quotes.py` memakai `if value is not None`, sehingga TIGA bentuk permintaan
yang berbeda maknanya tidak bisa dibedakan:

    kunci absen -> tidak diubah          (benar)
    null        -> DIABAIKAN, dijawab 200 (SALAH: pengguna meminta mengosongkan
                                           dan diberi tahu "berhasil", padahal
                                           nilainya tetap)
    ""          -> tersimpan sebagai string kosong

Akibatnya Pesanan menyimpan NULL dan Penawaran menyimpan '' untuk makna yang
SAMA. Dua bentuk data untuk satu arti, dan setiap pembaca harus tahu tabel mana
memakai bentuk mana.

Sesudah perbaikan, seragam dengan PATCH Pesanan (f1ce3564) dan faktur
(fd5a9dc5): absen = jangan ubah, null ATAU "" = NULL.

CAKUPAN YANG JUJUR: uji ini menempuh SKEMA + `model_dump`, yaitu tempat
perbaikannya berada, bukan permintaan HTTP. Gerbang HTTP-nya terhalang: akun
uji non-owner berperan Collaborator dan menerima 403 bahkan untuk MEMBACA
`/api/quotes`. Menaikkan perannya memperluas kredensial berdiri di tenant
produksi, jadi itu keputusan owner -- bukan sesuatu yang kuputuskan sendiri
demi mewarnai gerbang.
"""

import sys
from pathlib import Path

GW = Path(__file__).resolve().parents[2]
if str(GW) not in sys.path:
    sys.path.insert(0, str(GW))

from app.schemas.quotes import UpdateQuoteRequest  # noqa: E402


def _kolom(body: UpdateQuoteRequest) -> dict:
    """Persis yang dipakai router: dump tanpa field yang tak dikirim."""
    return body.model_dump(exclude_unset=True, exclude={"items", "dp_amount", "dp_percent"})


def test_kunci_absen_TIDAK_ikut_terkirim():
    kol = _kolom(UpdateQuoteRequest(subject="Halo"))
    assert "reference" not in kol, "field yang tak dikirim tak boleh ikut di-UPDATE"
    assert kol["subject"] == "Halo"


def test_null_eksplisit_IKUT_dan_bernilai_None():
    kol = _kolom(UpdateQuoteRequest(reference=None))
    assert "reference" in kol, (
        "null eksplisit HARUS ikut -- kalau tidak, permintaan mengosongkan "
        "dijawab 200 tanpa mengubah apa pun"
    )
    assert kol["reference"] is None


def test_string_kosong_menjadi_None_bukan_disimpan_apa_adanya():
    for nilai in ("", "   ", "\t"):
        kol = _kolom(UpdateQuoteRequest(reference=nilai))
        assert "reference" in kol
        assert kol["reference"] is None, (
            f"{nilai!r} harus jadi NULL, bukan disimpan sebagai string kosong"
        )


def test_ketiga_bentuk_itu_BERBEDA_satu_sama_lain():
    """Inti kontraknya: tiga bentuk, tiga hasil, tak boleh ada yang tertukar."""
    absen = _kolom(UpdateQuoteRequest(subject="x"))
    nul = _kolom(UpdateQuoteRequest(reference=None))
    kosong = _kolom(UpdateQuoteRequest(reference=""))

    assert "reference" not in absen
    assert nul.get("reference", "SENTINEL") is None
    assert kosong.get("reference", "SENTINEL") is None
    # null dan "" sengaja BERTEMU di NULL; yang absen harus tetap terpisah.
    assert ("reference" in nul) and ("reference" in kosong) and ("reference" not in absen)


def test_seragam_dengan_pesanan_penjualan():
    """Kalau SO dan Quote berbeda lagi, uji ini yang memberitahu."""
    from app.schemas.sales_orders import UpdateSalesOrderRequest

    so = UpdateSalesOrderRequest(reference="").model_dump(exclude_unset=True)
    q = _kolom(UpdateQuoteRequest(reference=""))
    assert so.get("reference") is None and q.get("reference") is None, (
        "Pesanan dan Penawaran harus menyimpan bentuk yang SAMA untuk arti yang sama"
    )


def test_nilai_biasa_tetap_lewat_utuh():
    """Kontrol positif: perbaikan ini tidak boleh menelan nilai yang sah."""
    kol = _kolom(UpdateQuoteRequest(reference="  PO-123  ", subject="Penawaran A"))
    assert kol["reference"] == "PO-123"
    assert kol["subject"] == "Penawaran A"
