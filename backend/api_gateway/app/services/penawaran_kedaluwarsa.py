"""Q-017 (26 Sep 2026): SATU aturan "penawaran kedaluwarsa" untuk daftar, detail, mendekati-kedaluwarsa & ringkasan.

kedaluwarsa = status menunggu jawaban (sent ATAU viewed — putusan MASTER: dilihat tapi tak dijawab lewat tanggal
tetap kedaluwarsa) DAN expiry_date < tanggal BISNIS tenant (bukan date.today()/CURRENT_DATE UTC server).
`hari_ini` WAJIB dari tanggal_dokumen(conn, tenant) dan dipakai SAMA oleh Python & SQL (satu tanggal per aksi).
Status tersimpan 'expired' (trigger check_quote_expiry) tidak diubah artinya.
"""
from datetime import date
from typing import Optional

MENUNGGU = ("sent", "viewed")


def kedaluwarsa(status: Optional[str], expiry_date: Optional[date], hari_ini: date) -> bool:
    return status in MENUNGGU and expiry_date is not None and expiry_date < hari_ini


def sql_kedaluwarsa(p: str) -> str:
    """Predikat SQL setara `kedaluwarsa` dengan hari_ini di parameter `p` (mis. "$2")."""
    return f"(status IN ('sent', 'viewed') AND expiry_date IS NOT NULL AND expiry_date < {p}::date)"


def sql_menunggu_aktif(p: str) -> str:
    """Menunggu jawaban & BELUM kedaluwarsa (tanpa tanggal = belum kedaluwarsa)."""
    return f"(status IN ('sent', 'viewed') AND (expiry_date IS NULL OR expiry_date >= {p}::date))"


# ---- Pill AKSI daftar Penawaran (13 Okt 2026, putusan pemilik; pola NetSuite "Pending …"/"Estimates to follow up").
# Server yang menentukan keanggotaan (FE tak menghitung tanggal). Satu aturan untuk baris daftar, ?aksi= dan /summary:
# Python `aksi_penawaran` dan SQL `sql_aksi` WAJIB setara (diuji berpasangan + harness nyata).
AMBANG_SEGERA_HABIS = 3  # hari; bawaan, kelak bisa diatur per tenant
AKSI = ("perlu_dikirim", "tunggu_jawaban", "segera_habis", "lewat_masa", "perlu_dipesan")


def aksi_penawaran(status: Optional[str], expiry_date: Optional[date], hari_ini: date,
                   converted_to_id=None, ambang: int = AMBANG_SEGERA_HABIS) -> Optional[str]:
    """Kunci aksi satu penawaran (atau None). Status tersimpan 'expired'/declined/converted/void -> None."""
    if status == "draft":
        return "perlu_dikirim"
    if status == "accepted":
        return "perlu_dipesan" if converted_to_id is None else None
    if status not in MENUNGGU:
        return None
    if expiry_date is None:
        return "tunggu_jawaban"
    if expiry_date < hari_ini:
        return "lewat_masa"
    if (expiry_date - hari_ini).days <= ambang:
        return "segera_habis"
    return "tunggu_jawaban"


def sql_aksi(aksi: str, p_hari: str, p_ambang: str) -> str:
    """Predikat SQL setara `aksi_penawaran(...) == aksi`; hari_ini di `p_hari`, ambang (int) di `p_ambang`."""
    batas = f"({p_hari}::date + {p_ambang}::int)"
    return {
        "perlu_dikirim": "(status = 'draft')",
        "tunggu_jawaban": f"(status IN ('sent', 'viewed') AND (expiry_date IS NULL OR expiry_date > {batas}))",
        "segera_habis": f"(status IN ('sent', 'viewed') AND expiry_date >= {p_hari}::date AND expiry_date <= {batas})",
        "lewat_masa": sql_kedaluwarsa(p_hari),
        "perlu_dipesan": "(status = 'accepted' AND converted_to_id IS NULL)",
    }[aksi]


def sql_aksi_berparam(aksi: str, mulai: int, hari_ini: date, ambang: int = AMBANG_SEGERA_HABIS) -> tuple:
    """(predikat, parameter) untuk SATU aksi mulai di $mulai -- hanya parameter yang BENAR-BENAR dirujuk predikat
    (asyncpg menolak $N yang tak dipakai: "could not determine data type of parameter")."""
    pakai = {"perlu_dikirim": (), "perlu_dipesan": (), "lewat_masa": (hari_ini,),
             "tunggu_jawaban": (hari_ini, ambang), "segera_habis": (hari_ini, ambang)}[aksi]
    return sql_aksi(aksi, f"${mulai}", f"${mulai + 1}"), list(pakai)
