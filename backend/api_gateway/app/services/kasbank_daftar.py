"""Daftar KAS & BANK standar SO (gelombang 1, kontrak backend/docs/KONTRAK-kasbank-standar-so-20261109.md; MASTER 9 Nov 2026; pola
services/biaya_daftar): kunci ?aksi=, teks posisi, aksi per baris, dokumen per baris, dan aksi_tersedia detail untuk tiga halaman --
uang masuk/keluar manual (/api/bank-transactions), transfer (/api/bank-transfers), rekening (/api/bank-accounts). Server menghitung, FE
hanya memformat. Python `aksi_semua_*` dan SQL `sql_aksi_*` WAJIB setara (uji berpasangan).

Uang = SUM(debit) - SUM(credit) jurnal (Law 1/16), tenant eksplisit (Law 24). Alasan aksi_tersedia = penentu yang SAMA dengan jalur tulis
(services/kasbank_tulis), bukan salinan. Tanpa aturan uang baru: batal tidak dikunci saldo (K3), minus hanya isyarat di pratinjau."""
from decimal import Decimal
from typing import Optional

from ..schemas.aksi_tersedia import aksi
from . import kasbank_tulis as KT
from . import teks_galat as tg
from .bayar_keluar_rencana import Blok

SEN = Decimal("0.01")
TIPE_KARTU = "credit_card"


def rp(x) -> str:
    return str(Decimal(str(x or 0)).quantize(SEN))


def _bulan(kolom: str, p_hari: str) -> str:
    return f"date_trunc('month', {kolom}) = date_trunc('month', {p_hari}::date)"


def _pertama(blok: list) -> Optional[str]:
    return blok[0]["message"] if blok else None


# ---------------------------------------------------------------- uang masuk/keluar manual
# Baris = mutasi manual SAH (KT.ASAL_MANUAL -- predikat SAMA dengan void). Nominal + arah dari baris jurnal CoA rekening; akun lawan =
# baris jurnal pertama yang bukan CoA rekening. Batal = jurnal asli sudah dibalik atau baris VOIDED (cermin manual_void bukan baris daftar).
SQL_MANUAL = f"""
SELECT bt.id, COALESCE(bt.transaction_number, je.journal_number) AS nomor, bt.transaction_date AS tgl, bt.created_at,
       bt.description, NULLIF(btrim(bt.payee_payer), '') AS pihak, bt.journal_id, je.journal_number, je.created_at AS je_at,
       je.created_by AS je_by, je.reversed_by_id, bt.voided_at, bt.voided_by, bt.void_reason,
       ba.id AS ba_id, ba.account_name, ba.account_type,
       COALESCE(jb.net, 0) AS net, lw.id AS lawan_id, lw.account_code AS lawan_kode, lw.name AS lawan_nama,
       (je.reversed_by_id IS NOT NULL OR bt.status = 'VOIDED') AS batal
FROM bank_transactions bt
JOIN bank_accounts ba ON ba.id = bt.bank_account_id AND ba.tenant_id = bt.tenant_id
JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = bt.tenant_id
LEFT JOIN LATERAL (SELECT SUM(jl.debit - jl.credit) AS net FROM journal_lines jl
                   WHERE jl.journal_id = je.id AND jl.account_id = ba.coa_id) jb ON true
LEFT JOIN LATERAL (SELECT c.id, c.account_code, c.name FROM journal_lines jl
                   JOIN chart_of_accounts c ON c.id = jl.account_id AND c.tenant_id = bt.tenant_id
                   WHERE jl.journal_id = je.id AND jl.account_id <> ba.coa_id ORDER BY jl.line_number LIMIT 1) lw ON true
WHERE bt.tenant_id = $1 AND {KT.ASAL_MANUAL}"""

KARTU_MANUAL = ("masuk_bulan_ini", "keluar_bulan_ini")  # kartu; TANPA pill (putusan MASTER 9 Nov)
STATUS_MANUAL = ("tercatat", "dibatalkan", "semua")


def sql_aksi_manual(kunci: str, p_hari: str, a: str = "d") -> str:
    """Predikat atas baris SQL_MANUAL (alias a)."""
    return {
        "masuk_bulan_ini": f"(NOT {a}.batal AND {a}.net > 0 AND {_bulan(a + '.tgl', p_hari)})",
        "keluar_bulan_ini": f"(NOT {a}.batal AND {a}.net < 0 AND {_bulan(a + '.tgl', p_hari)})",
    }[kunci]


def sql_status_manual(status: str, a: str = "d") -> Optional[str]:
    return {"tercatat": f"NOT {a}.batal", "dibatalkan": f"{a}.batal", "semua": None}[status]


def arah(net) -> str:
    return "masuk" if Decimal(str(net or 0)) > 0 else "keluar"


def position_text_manual(net, rekening: str, batal: bool, alasan: Optional[str] = None) -> str:
    if batal:
        a = (alasan or "").strip()
        return f"Dibatalkan · {a[:60]}" if a else "Dibatalkan"
    return f"Masuk ke {rekening}" if arah(net) == "masuk" else f"Keluar dari {rekening}"


def documents_baris_manual(batal: bool) -> list:
    """mutasi itu sendiri · jurnal · jurnal pembalik (bila batal)."""
    return [{"type": "bank_transaction"}, {"type": "journal"}] + ([{"type": "journal"}] if batal else [])


def baris_manual(r) -> dict:
    net = Decimal(str(r["net"] or 0))
    return {
        "id": str(r["id"]), "nomor": r["nomor"], "transaction_date": r["tgl"].isoformat() if r["tgl"] else None,
        "arah": arah(net), "amount": rp(abs(net)),
        "bank_account": {"id": str(r["ba_id"]), "account_name": r["account_name"], "account_type": r["account_type"]},
        "akun_lawan": ({"id": str(r["lawan_id"]), "kode": r["lawan_kode"], "nama": r["lawan_nama"]} if r["lawan_id"] else None),
        "pihak": r["pihak"], "description": r["description"], "status": "dibatalkan" if r["batal"] else "tercatat",
        "position_text": position_text_manual(net, r["account_name"], r["batal"], r["void_reason"]),
        "aksi": None, "aksi_semua": [],
        "documents": documents_baris_manual(r["batal"]), "doc_count": len(documents_baris_manual(r["batal"])),
        "journal_id": str(r["journal_id"]) if r["journal_id"] else None, "journal_number": r["journal_number"],
    }


async def aksi_tersedia_manual(conn, tid: str, tx_id) -> dict:
    """batalkan = penentu void (KT.rencana_void_manual); ubah = tak pernah (Law 2: mutasi tercatat tidak diubah)."""
    r = await KT.rencana_void_manual(conn, tid, tx_id, None, Blok(True))
    return {
        "batalkan": aksi(not r["blocks"], _pertama(r["blocks"])),
        "ubah": aksi(False, "Mutasi tercatat tidak bisa diubah; batalkan lalu catat ulang."),
    }


# ---------------------------------------------------------------- transfer
AKSI_TRANSFER = ("draf",)          # pill (hitung), desak 1
KARTU_TRANSFER = ("bulan_ini",)    # kartu ringkasan, bukan pill
SEMUA_KUNCI_TRANSFER = AKSI_TRANSFER + KARTU_TRANSFER


def sql_aksi_transfer(kunci: str, p_hari: str, a: str = "bt") -> str:
    return {
        "draf": f"{a}.status = 'draft'",
        "bulan_ini": f"({a}.status <> 'void' AND {_bulan(a + '.transfer_date', p_hari)})",
    }[kunci]


def aksi_semua_transfer(status: Optional[str]) -> list:
    return ["draf"] if status == "draft" else []


def position_text_transfer(status: Optional[str], asal: Optional[str], tujuan: Optional[str]) -> str:
    if status == "draft":
        return "Draf · belum diposting"
    if status == "void":
        return "Dibatalkan"
    return f"Diposting · {asal} → {tujuan}"


def documents_baris_transfer(status: Optional[str], journal_id) -> list:
    """transfer · jurnal (+pembalik bila batal) · mutasi bank keluar+masuk (+cerminnya bila batal)."""
    if not journal_id:
        return [{"type": "bank_transfer"}]
    batal = status == "void"
    return ([{"type": "bank_transfer"}, {"type": "journal"}] + ([{"type": "journal"}] if batal else [])
            + [{"type": "bank_transaction"}] * (4 if batal else 2))


async def aksi_tersedia_transfer(conn, tid: str, t) -> dict:
    """posting = penentu posting (KT.rencana_posting_transfer_id: KAS_MINUS, saldo, periode, akun biaya); batalkan = penentu void."""
    draf = t["status"] == "draft"
    post = (await KT.rencana_posting_transfer_id(conn, tid, t["id"]))["blocks"] if draf else [
        {"message": "Hanya transfer draf yang bisa diposting."}]
    batal = (await KT.rencana_void_transfer(conn, tid, t["id"], None, Blok(True)))["blocks"]
    return {
        "posting": aksi(not post, _pertama(post)),
        "ubah": aksi(draf, "Transfer yang sudah diposting tidak bisa diubah; batalkan lalu buat baru."),
        "hapus": aksi(draf, "Transfer yang sudah diposting tidak bisa dihapus; batalkan saja."),
        "batalkan": aksi(not draf and not batal, "Draf dihapus, bukan dibatalkan." if draf else _pertama(batal)),
    }


# ---------------------------------------------------------------- rekening
AKSI_REKENING = ("saldo_minus",)   # pill
KARTU_REKENING = ("nonaktif",)
SEMUA_KUNCI_REKENING = AKSI_REKENING + KARTU_REKENING


def sql_saldo(a: str = "ba") -> str:
    """Saldo jurnal POSTED CoA rekening (Law 1/16; == kasbank_ringkas/kasbank_tulis), tenant eksplisit."""
    return (f"(SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
            f"WHERE jl.account_id = {a}.coa_id AND je.status = 'POSTED' AND je.tenant_id = {a}.tenant_id)")


def sql_aksi_rekening(kunci: str, a: str = "ba") -> str:
    return {
        "saldo_minus": f"({a}.is_active AND {a}.account_type <> '{TIPE_KARTU}' AND {sql_saldo(a)} < 0)",
        "nonaktif": f"NOT {a}.is_active",
    }[kunci]


def aksi_semua_rekening(is_active: bool, tipe: Optional[str], saldo) -> list:
    return ["saldo_minus"] if is_active and tipe != TIPE_KARTU and Decimal(str(saldo or 0)) < 0 else []


def position_text_rekening(is_active: bool, tipe: Optional[str], saldo, is_default: bool = False) -> str:
    s = Decimal(str(saldo or 0))
    if not is_active:
        return "Nonaktif"
    if tipe == TIPE_KARTU:  # CoA LIABILITY: saldo debit-kredit negatif = terutang
        return f"Terutang {tg.rp(abs(s))}" if s < 0 else "Lunas"
    if s < 0:
        return "Saldo minus"
    return "Aktif · utama" if is_default else "Aktif"


async def jumlah_mutasi_nyata(conn, tid: str, ba_id) -> int:
    """Penentu hapus rekening (== delete_bank_account): mutasi selain saldo awal -> hapus = nonaktifkan; tanpa -> hapus permanen."""
    return await conn.fetchval("SELECT COUNT(*) FROM bank_transactions WHERE bank_account_id = $1 AND tenant_id = $2 "
                               "AND transaction_type != 'opening'", ba_id, tid)


async def coa_terbukukan(conn, tid: str, ba) -> bool:
    """CoA rekening dipakai baris jurnal yang TAK BISA dihapus jalur hapus permanen (semua kecuali jurnal 'OPENING' non-POSTED milik
    rekening ini): saldo awal BANK_OPENING_BALANCE / jurnal POSTED. Dulu hapus permanen lalu gagal FK journal_lines /
    trg_prevent_posted_journal_delete -> 500 + rollback (putusan A MASTER 10 Nov: kini 400 berkalimat, hasil DB identik)."""
    coa = ba.get("coa_id")
    if not coa:
        return False
    return await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                          WHERE jl.account_id = $1 AND je.tenant_id = $2
                            AND NOT (je.source_type = 'OPENING' AND je.source_id = $3 AND je.status <> 'POSTED'))""",
        coa, tid, ba["id"]) is True


def alasan_hapus_rekening(is_active: bool, jumlah_mutasi: int, terbukukan: bool = False) -> Optional[str]:
    """None = boleh (permanen bila tanpa mutasi, nonaktifkan bila bermutasi). Sama dengan galat delete_bank_account.
    terbukukan (hanya berarti bila tanpa mutasi) = CoA dipakai jurnal saldo awal/POSTED -> permanen tak mungkin."""
    if jumlah_mutasi > 0 and not is_active:
        return "Rekening ini sudah nonaktif dan punya mutasi, jadi tidak bisa dihapus permanen."
    if jumlah_mutasi == 0 and terbukukan:
        return ("Rekening ini sudah nonaktif dan punya saldo awal yang sudah dibukukan, jadi tidak bisa dihapus permanen." if not is_active
                else "Rekening punya saldo awal yang sudah dibukukan, jadi tidak bisa dihapus permanen. Nonaktifkan saja.")
    return None


async def aksi_tersedia_rekening(conn, tid: str, ba) -> dict:
    aktif = bool(ba["is_active"])
    mati = "Rekening ini nonaktif."  # == blok REKENING_NONAKTIF penentu B8
    n = await jumlah_mutasi_nyata(conn, tid, ba["id"])
    hapus = alasan_hapus_rekening(aktif, n, n == 0 and await coa_terbukukan(conn, tid, ba))  # == delete_bank_account
    return {
        "uang_masuk": aksi(aktif, mati), "uang_keluar": aksi(aktif, mati), "transfer": aksi(aktif, mati),
        "hitung_fisik": aksi(aktif, "Rekening nonaktif tidak bisa disesuaikan."),
        "ubah": aksi(True, None),
        "nonaktifkan": aksi(aktif, "Rekening ini sudah nonaktif."),
        "aktifkan": aksi(not aktif, "Rekening ini sudah aktif."),
        "hapus": aksi(hapus is None, hapus),
    }
