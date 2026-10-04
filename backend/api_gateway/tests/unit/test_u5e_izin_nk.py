"""U5-E (4 Okt 2026): izin Nota Kredit BERTINGKAT per langkah -- "Pola standar" yang dipilih pemilik LANGSUNG.

Aturan: post=P, refund=P, void=V, unapply=V, apply=A; pratinjau = izin tulisnya; buat draf (POST /api/credit-notes,
/preview) + create-tax-invoice tetap C; baca R; ubah U; hapus D. Dulu SEMUA POST jatuh ke prefiks = C, sehingga peran yang
hanya punya C (STORE_STAFF, SALES) bisa menerbitkan/membatalkan NK sementara CASHIER/TAX_OFFICER (punya P) tak bisa.

Tabel peran->aksi di bawah = HASIL BACAAN role_permissions prod (modul CREDIT_NOTE, 4 Okt 2026, peran sistem __SYSTEM__);
ia KONSTAN uji, bukan sumber kebenaran -- bukti pembacaan hidup ada di skrip bukti (PolicyEngine nyata terhadap DB) yang
dijalankan sebelum rilis. Tes ini mengunci PEMETAAN rute -> huruf dan akibatnya pada tiap peran bawaan."""
import re
from pathlib import Path

import pytest

from app.middleware import permission_middleware as PM

INV = Path(__file__).with_name("inventaris_rute_tulis.txt")

# rute -> (metode, jalur, huruf)
PETA = [
    ("POST", "/api/credit-notes", "C"),                       # buat draf
    ("POST", "/api/credit-notes/preview", "C"),               # pratinjau draf = tulisnya
    ("POST", "/api/credit-notes/X/post", "P"),
    ("POST", "/api/credit-notes/X/post/preview", "P"),
    ("POST", "/api/credit-notes/X/refund", "P"),
    ("POST", "/api/credit-notes/X/void", "V"),
    ("POST", "/api/credit-notes/X/void/preview", "V"),
    ("POST", "/api/credit-notes/X/apply/preview", "A"),    # U10: pratinjau ikut izin tulisnya
    ("POST", "/api/credit-notes/X/unapply/preview", "V"),
    ("POST", "/api/credit-notes/X/refund/preview", "P"),
    ("POST", "/api/credit-notes/X/unapply", "V"),
    ("POST", "/api/credit-notes/X/apply", "A"),
    ("POST", "/api/credit-notes/X/create-tax-invoice", "C"),  # tak disebut pemilik -> tetap prefiks C (lihat catatan)
    ("PATCH", "/api/credit-notes/X", "U"),
    ("DELETE", "/api/credit-notes/X", "D"),
    ("GET", "/api/credit-notes", "R"),
    ("GET", "/api/credit-notes/summary", "R"),
    ("GET", "/api/credit-notes/X", "R"),
    ("GET", "/api/credit-notes/X/history", "R"),
]

# Peran sistem -> aksi CREDIT_NOTE (dibaca dari role_permissions prod, 4 Okt 2026)
PERAN = {
    "OWNER": set("CRUDVAPE"), "ADMIN": set("CRUDVAPE"), "FINANCE_MGR": set("CRUDVAPE"), "BENDAHARA": set("CRUVAPE"),
    "ACCOUNTANT": set("CRUPE"), "CASHIER": set("RP"), "TAX_OFFICER": set("RP"),
    "SALES": set("CRUE"), "STORE_STAFF": set("CRU"),
    "VIEWER": set("R"), "COLLABORATOR": set("R"), "PRODUCTION_MGR": set("R"),
}
# langkah -> (metode, jalur) yang dipakai matriks
LANGKAH = {
    "buat_draf": ("POST", "/api/credit-notes"),
    "terbitkan": ("POST", "/api/credit-notes/X/post"),
    "refund": ("POST", "/api/credit-notes/X/refund"),
    "batalkan": ("POST", "/api/credit-notes/X/void"),
    "lepas_penerapan": ("POST", "/api/credit-notes/X/unapply"),
    "terapkan": ("POST", "/api/credit-notes/X/apply"),
    "lihat": ("GET", "/api/credit-notes/X"),
}
# yang BOLEH tiap peran (ditulis TANGAN dari kebijakan pemilik, bukan diturunkan dari kode di atas)
BOLEH = {
    "OWNER": set(LANGKAH), "ADMIN": set(LANGKAH), "FINANCE_MGR": set(LANGKAH), "BENDAHARA": set(LANGKAH) - set(),
    "ACCOUNTANT": {"buat_draf", "terbitkan", "refund", "lihat"},          # punya P, tak punya V/A
    "CASHIER": {"terbitkan", "refund", "lihat"},                           # punya P, tak punya C
    "TAX_OFFICER": {"terbitkan", "refund", "lihat"},
    "SALES": {"buat_draf", "lihat"},                                       # dulu: bisa terbitkan/batalkan lewat C
    "STORE_STAFF": {"buat_draf", "lihat"},
    "VIEWER": {"lihat"}, "COLLABORATOR": {"lihat"}, "PRODUCTION_MGR": {"lihat"},
}


def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


@pytest.mark.parametrize("metode,jalur,huruf", PETA)
def test_pemetaan_rute_ke_huruf(metode, jalur, huruf):
    assert _izin(metode, jalur) == ("credit_note", huruf)


@pytest.mark.parametrize("peran", sorted(PERAN))
def test_matriks_peran_kali_langkah(peran):
    aksi = PERAN[peran]
    hasil = set()
    for nama, (metode, jalur) in LANGKAH.items():
        modul, huruf = _izin(metode, jalur)
        assert modul == "credit_note"
        if huruf in aksi:
            hasil.add(nama)
    assert hasil == BOLEH[peran], f"{peran}: boleh={sorted(hasil)} seharusnya={sorted(BOLEH[peran])}"


def test_perubahan_nyata_peran_yang_hanya_punya_c_tak_lagi_bisa_terbitkan_batalkan():
    # inti keluhan: C saja = hanya buat draf
    for peran in ("STORE_STAFF", "SALES"):
        assert {"terbitkan", "batalkan", "terapkan", "refund", "lepas_penerapan"}.isdisjoint(BOLEH[peran])
        assert "buat_draf" in BOLEH[peran]
    # sebaliknya peran ber-P (CASHIER/TAX_OFFICER) kini BISA menerbitkan -- dan refund (refund=P, putusan pemilik)
    assert {"terbitkan", "refund"} <= BOLEH["CASHIER"] and "batalkan" not in BOLEH["CASHIER"]


def test_setiap_rute_tulis_nk_di_inventaris_punya_keputusan_eksplisit():
    """Rute POST /api/credit-notes/* baru TANPA keputusan huruf = merah (dulu diam-diam jatuh ke C)."""
    diputuskan = {"post": "P", "post/preview": "P", "refund": "P", "void": "V", "void/preview": "V", "unapply": "V",
                  "apply": "A", "create-tax-invoice": "C",
                  "apply/preview": "A", "unapply/preview": "V", "refund/preview": "P"}  # U10: pratinjau = izin tulisnya
    inv = [b.strip() for b in INV.read_text(encoding="utf-8").splitlines() if b.strip() and not b.lstrip().startswith("#")]
    nk = [b for b in inv if b.startswith("POST /api/credit-notes")]
    assert len(nk) >= 8, nk  # kontrol positif: inventaris terbaca
    tak = []
    for b in nk:
        jalur = b.split(" ", 1)[1]
        m = re.fullmatch(r"/api/credit-notes(?:/preview|/\{credit_note_id\}/(.+))?", jalur)
        if m is None:
            tak.append(b)
            continue
        sufiks = m.group(1)
        if sufiks is None:  # buat draf (/api/credit-notes) / pratinjau draf (/api/credit-notes/preview)
            seharusnya = "C"
        else:
            seharusnya = diputuskan.get(sufiks)
        if seharusnya is None or _izin("POST", re.sub(r"\{[^}]+\}", "X", jalur)) != ("credit_note", seharusnya):
            tak.append(b)
    assert not tak, f"rute tulis NK tanpa keputusan izin yang cocok: {tak}"
