"""Jenis penerimaan uang muka pelanggan (W-20261014-DEPOSIT-JENIS, V422, 14 Okt 2026): 'DP' | 'PELUNASAN'.

HANYA LABEL. Pembukuan tetap customer deposit (pola NetSuite): plafon, posting, penerapan, refund, jurnal TIDAK
membaca jenis. Pembayaran sebelum faktur bisa langsung pelunasan -> layar "Terima pembayaran".

Jenis EFEKTIF (putusan MASTER A, tanpa backfill) = cd.jenis tersimpan -> purpose proforma yang ditunjuk
(DP/TERMIN -> DP, PELUNASAN -> PELUNASAN) -> 'DP'. Purpose proforma = dokumen terbit tersimpan -> turunan stabil.
Jenis yang dikirim eksplisit MENANG walau berbeda dengan purpose proforma (putusan B; label saja, tanpa galat).
Python (jenis_efektif) dan SQL (sql_jenis_efektif) WAJIB sama: dikunci tests/unit/test_deposit_jenis.py +
harness nyata scripts/ops/uji-nyata-deposit-jenis.py (SQL == Python atas SEMUA baris).
"""
from typing import Optional

PENANDA_DEPOSIT_JENIS = "deposit-jenis-v422-w20261014"

JENIS = ("DP", "PELUNASAN")
JENIS_BAWAAN = "DP"
# purpose proforma -> jenis penerimaan
PETA_PURPOSE = {"DP": "DP", "TERMIN": "DP", "PELUNASAN": "PELUNASAN"}

# kalimat riwayat ("Uang muka DEP-.. diterima" vs "Pelunasan DEP-.. diterima")
LABEL_JENIS = {"DP": "Uang muka", "PELUNASAN": "Pelunasan"}
# keperluan pada kwitansi ("Untuk <label>")
LABEL_KWITANSI = {"DP": "Uang Muka", "PELUNASAN": "Pelunasan"}


def jenis_dari_purpose(purpose: Optional[str]) -> str:
    return PETA_PURPOSE.get(purpose or "", JENIS_BAWAAN)


def jenis_efektif(jenis: Optional[str], purpose: Optional[str]) -> str:
    """jenis tersimpan menang; NULL (baris lama) -> turunan purpose proforma -> 'DP'."""
    if jenis in JENIS:
        return jenis
    return jenis_dari_purpose(purpose)


def sql_jenis_efektif(alias: str) -> str:
    """Ekspresi SQL jenis efektif untuk baris customer_deposits ber-alias `alias` (nama tabel pun boleh).
    Proforma disaring tenant baris itu sendiri (gateway = BYPASSRLS; RLS bukan pagar)."""
    kasus = " ".join(f"WHEN '{p}' THEN '{j}'" for p, j in PETA_PURPOSE.items())
    return (
        f"COALESCE({alias}.jenis, (SELECT CASE pfj.purpose {kasus} END FROM proformas pfj "
        f"WHERE pfj.id = {alias}.proforma_id AND pfj.tenant_id = {alias}.tenant_id), '{JENIS_BAWAAN}')"
    )


def label_jenis(jenis: Optional[str]) -> str:
    return LABEL_JENIS.get(jenis or "", LABEL_JENIS[JENIS_BAWAAN])


def label_ringkas(ringkas: str, label: Optional[str]) -> str:
    """Ringkas audit uang muka TERSIMPAN ('Uang muka DEP-.. dibatalkan', 'Penerapan uang muka ..') -> label jenis
    efektif SAAT DIBACA. Hanya awalan; label None/'Uang muka' -> tak berubah."""
    dasar = LABEL_JENIS[JENIS_BAWAAN]
    if not label or label == dasar or not ringkas:
        return ringkas
    if ringkas.startswith(dasar):
        return label + ringkas[len(dasar):]
    awal = f"Penerapan {dasar.lower()}"
    if ringkas.startswith(awal):
        return f"Penerapan {label.lower()}" + ringkas[len(awal):]
    return ringkas


def label_kwitansi(jenis: Optional[str], ada_so: bool) -> str:
    """Keperluan kwitansi: 'Uang Muka Pesanan' / 'Pelunasan Pesanan'; tanpa SO: 'Uang Muka' / 'Pelunasan'."""
    dasar = LABEL_KWITANSI.get(jenis or "", LABEL_KWITANSI[JENIS_BAWAAN])
    return f"{dasar} Pesanan" if ada_so else dasar


# ── PELUNASAN-TEKS (15 Okt 2026, putusan MASTER #2): teks yang menyebut SATU penerimaan memakai label jenis efektifnya ──
# ("Uang muka" | "Pelunasan"). Konsep SALDO (akun "Uang Muka Pelanggan", "saldo uang muka pelanggan", penjaga "uang muka aktif")
# tetap "uang muka" -- pelunasan yang belum diterapkan pun duduk di akun itu. Jurnal LAMA tak disentuh (Law 2); memo jurnal BARU
# memakai label_kwitansi-kapital ("Terima Uang Muka - .." tetap sama untuk DP).

async def label_deposit(conn, tenant_id: str, deposit_id, kapital: bool = False) -> str:
    """Label jenis efektif satu uang muka/pelunasan (tenant eksplisit). kapital=True -> 'Uang Muka' / 'Pelunasan' (memo jurnal)."""
    j = await conn.fetchval(
        f"SELECT {sql_jenis_efektif('cdl')} FROM customer_deposits cdl WHERE cdl.id = $1 AND cdl.tenant_id = $2",
        deposit_id, tenant_id)
    return LABEL_KWITANSI.get(j or "", LABEL_KWITANSI[JENIS_BAWAAN]) if kapital else label_jenis(j)


async def label_deposit_banyak(conn, tenant_id: str, ids) -> dict:
    """{deposit_id: label} untuk banyak baris sekaligus (satu kueri)."""
    ids = [i for i in ids if i is not None]
    if not ids:
        return {}
    rows = await conn.fetch(
        f"SELECT cdl.id, {sql_jenis_efektif('cdl')} AS jenis FROM customer_deposits cdl "
        "WHERE cdl.tenant_id = $1 AND cdl.id = ANY($2::uuid[])", tenant_id, list(ids))
    return {r["id"]: label_jenis(r["jenis"]) for r in rows}


def frasa_daftar(potongan: list) -> str:
    """['Uang muka DEP-1 (..)', 'Pelunasan DEP-2 (..)'] -> 'Uang muka DEP-1 (..), pelunasan DEP-2 (..)' (huruf besar di awal saja)."""
    if not potongan:
        return ""
    teks = ", ".join(x[:1].lower() + x[1:] for x in potongan)
    return teks[:1].upper() + teks[1:]
