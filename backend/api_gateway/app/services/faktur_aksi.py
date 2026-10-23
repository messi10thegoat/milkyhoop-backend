"""Pill AKSI daftar Faktur Penjualan (14 Okt 2026, MASTER; pola NetSuite/Xero Draft / Awaiting Payment / Overdue).

Server menentukan keanggotaan (FE tak menghitung tanggal), SATU aturan untuk baris daftar, ?aksi= dan /summary:
Python `aksi_faktur` dan SQL `sql_aksi` WAJIB setara (diuji berpasangan + harness nyata). Kunci SALING EKSKLUSIF:

  perlu_diterbitkan = status draft
  jatuh_tempo       = terbit, sisa > 0, jatuh tempo EFEKTIF < hari ini
  segera_tempo      = terbit, sisa > 0, hari ini <= jatuh tempo efektif <= hari ini + N
  belum_lunas       = terbit, sisa > 0, jatuh tempo efektif > hari ini + N
  N = pengingat.ambang_segera (accounting_settings.pengingat_segera_hari, V450; bawaan 7) -- SATU definisi "segera jatuh tempo"
  (putusan pemilik LANGSUNG 22 Okt; dulu 3 hari)
  (lunas / void = bukan aksi)

Sisa = compute_ar_outstanding (turunan jurnal, Iron Law 1/16/29) -- SATU sumber dengan ?tugas=belum_lunas /
outstanding-summary. belum_lunas + segera_tempo + jatuh_tempo == dashboard_v2.pilih_belum_lunas.
Jatuh tempo efektif = due_date, bila kosong = invoice_date (putusan MASTER 14 Okt, pola "due on receipt"). Hari ini =
tanggal BISNIS tenant (tanggal_dokumen), dipakai SAMA oleh Python & SQL.
CATATAN: ?tugas=telat / kartu dashboard / ?status=overdue / laporan umur TETAP memakai aturan lamanya (due kosong =
telat atau tak telat) -- penyelarasan = tiket terpisah; hari ini 0 baris due kosong, jadi tak ada selisih angka.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

AMBANG_SEGERA_TEMPO = 7  # bawaan == pengingat.AMBANG_BAWAAN; nilai tenant = pengingat.ambang_segera (pemanggil WAJIB meneruskannya)
AKSI = ("perlu_diterbitkan", "belum_lunas", "segera_tempo", "jatuh_tempo")

SQL_JATUH_TEMPO_EFEKTIF = "COALESCE(si.due_date, si.invoice_date)"
# Sisa positif untuk WHERE daftar (tak ada join compute_ar_outstanding di COUNT/daftar); $1 = tenant_id.
SQL_SISA_POSITIF_SUBQUERY = (
    "si.id IN (SELECT invoice_id FROM compute_ar_outstanding($1) WHERE invoice_id IS NOT NULL AND outstanding > 0)"
)
# Untuk query ringkasan yang sudah LEFT JOIN ar_fn.
SQL_SISA_POSITIF_JOIN = "COALESCE(ar_fn.outstanding, 0) > 0"


def jatuh_tempo_efektif(due_date: Optional[date], invoice_date: Optional[date]) -> Optional[date]:
    return due_date if due_date is not None else invoice_date


def aksi_faktur(status: Optional[str], sisa, due_date: Optional[date], invoice_date: Optional[date],
                hari_ini: date, ambang: int = AMBANG_SEGERA_TEMPO) -> Optional[str]:
    """Kunci aksi satu faktur (atau None). `sisa` = turunan jurnal (None/<=0 = lunas atau draf/void)."""
    if status == "draft":
        return "perlu_diterbitkan"
    if status in (None, "void") or sisa is None or Decimal(str(sisa)) <= 0:
        return None
    eff = jatuh_tempo_efektif(due_date, invoice_date)
    if eff is None:
        return None
    if eff < hari_ini:
        return "jatuh_tempo"
    if eff <= hari_ini + timedelta(days=ambang):
        return "segera_tempo"
    return "belum_lunas"


def sql_aksi(aksi: str, p_hari: str, p_ambang: str, sisa_positif: str = SQL_SISA_POSITIF_SUBQUERY) -> str:
    """Predikat SQL setara `aksi_faktur(...) == aksi` (alias tabel `si`); hari_ini di `p_hari`, ambang di `p_ambang`."""
    terbit = f"(si.status NOT IN ('draft', 'void') AND {sisa_positif})"
    eff = SQL_JATUH_TEMPO_EFEKTIF
    batas = f"({p_hari}::date + {p_ambang}::int)"
    return {
        "perlu_diterbitkan": "(si.status = 'draft')",
        "jatuh_tempo": f"({terbit} AND {eff} < {p_hari}::date)",
        "segera_tempo": f"({terbit} AND {eff} >= {p_hari}::date AND {eff} <= {batas})",
        "belum_lunas": f"({terbit} AND {eff} > {batas})",
    }[aksi]


def sql_aksi_berparam(aksi: str, mulai: int, hari_ini: date, ambang: int = AMBANG_SEGERA_TEMPO) -> tuple:
    """(predikat, parameter) untuk SATU aksi mulai di $mulai -- hanya parameter yang BENAR-BENAR dirujuk predikat
    (asyncpg menolak $N yang tak dipakai: "could not determine data type of parameter")."""
    pakai = {"perlu_diterbitkan": (), "jatuh_tempo": (hari_ini,),
             "segera_tempo": (hari_ini, ambang), "belum_lunas": (hari_ini, ambang)}[aksi]
    return sql_aksi(aksi, f"${mulai}", f"${mulai + 1}"), list(pakai)


def sql_nilai(aksi: str) -> str:
    """Kolom nilai ringkasan (query ber-JOIN ar_fn): draf = total_amount, terbit = SISA tagihan journal-derived."""
    return "si.total_amount" if aksi == "perlu_diterbitkan" else "ar_fn.outstanding"


async def ringkas_aksi(conn, tenant_id: str, hari_ini: date, ambang: int, id_cari=None) -> dict:
    """{kunci: {count, amount Decimal}} per AKSI -- SATU sumber /sales-invoices/summary DAN pengingat dashboard (22 Okt 2026).
    Nilai: draf = total, terbit = SISA (compute_ar_outstanding)."""
    row = await conn.fetchrow(
        "WITH ar_fn AS (SELECT invoice_id, outstanding FROM compute_ar_outstanding($1)) SELECT "
        + ", ".join(
            f"COUNT(*) FILTER (WHERE {sql_aksi(k, '$2', '$3', SQL_SISA_POSITIF_JOIN)}) AS {k}_count, "
            f"COALESCE(SUM({sql_nilai(k)}) FILTER (WHERE {sql_aksi(k, '$2', '$3', SQL_SISA_POSITIF_JOIN)}), 0) AS {k}_value"
            for k in AKSI)
        + " FROM sales_invoices si LEFT JOIN ar_fn ON ar_fn.invoice_id = si.id WHERE si.tenant_id = $1"
        + ("" if id_cari is None else " AND si.id = ANY($4::uuid[])"),
        tenant_id, hari_ini, ambang, *([] if id_cari is None else [id_cari]))
    return {k: {"count": int(row[f"{k}_count"]), "amount": Decimal(str(row[f"{k}_value"]))} for k in AKSI}


# ---- T2 (23 Okt 2026, KONTRAK-dashboard-T2 §5): kunci SARING umur_* -- BUKAN pill (partisi AKSI & nilai `aksi` baris tak berubah) ----
# Umur dari jatuh tempo EFEKTIF via rumus TUNGGAL services/jatuh_tempo (kelas_umur / sql_umur, dipakai juga daftar tagihan & dashboard).
# Anggota = faktur TERBIT bersisa > 0 (sama dengan payung jatuh_tempo + segera_tempo + belum_lunas).
def umur_faktur(status: Optional[str], sisa, due_date: Optional[date], invoice_date: Optional[date], hari_ini: date) -> Optional[str]:
    from .jatuh_tempo import kelas_umur
    if status in (None, "draft", "void") or sisa is None or Decimal(str(sisa)) <= 0:
        return None
    return kelas_umur(jatuh_tempo_efektif(due_date, invoice_date), hari_ini)


def sql_umur(kunci: str, p_hari: str, sisa_positif: str = SQL_SISA_POSITIF_SUBQUERY) -> str:
    """Predikat SQL setara umur_faktur(...) == kunci (alias `si`)."""
    from . import jatuh_tempo as JT
    return f"(si.status NOT IN ('draft', 'void') AND {sisa_positif} AND {JT.sql_umur(kunci, SQL_JATUH_TEMPO_EFEKTIF, p_hari)})"


def sql_umur_berparam(kunci: str, mulai: int, hari_ini: date) -> tuple:
    return sql_umur(kunci, f"${mulai}"), [hari_ini]


async def ringkas_umur(conn, tenant_id: str, hari_ini: date, id_cari=None) -> dict:
    """{umur_*: {count, amount Decimal}} -- nilai = SISA compute_ar_outstanding; SATU kueri, pola ringkas_aksi."""
    from .jatuh_tempo import UMUR_KUNCI
    row = await conn.fetchrow(
        "WITH ar_fn AS (SELECT invoice_id, outstanding FROM compute_ar_outstanding($1)) SELECT "
        + ", ".join(
            f"COUNT(*) FILTER (WHERE {sql_umur(k, '$2', SQL_SISA_POSITIF_JOIN)}) AS {k}_count, "
            f"COALESCE(SUM(ar_fn.outstanding) FILTER (WHERE {sql_umur(k, '$2', SQL_SISA_POSITIF_JOIN)}), 0) AS {k}_value"
            for k in UMUR_KUNCI)
        + " FROM sales_invoices si LEFT JOIN ar_fn ON ar_fn.invoice_id = si.id WHERE si.tenant_id = $1"
        + ("" if id_cari is None else " AND si.id = ANY($3::uuid[])"),
        tenant_id, hari_ini, *([] if id_cari is None else [id_cari]))
    return {k: {"count": int(row[f"{k}_count"]), "amount": Decimal(str(row[f"{k}_value"]))} for k in UMUR_KUNCI}
