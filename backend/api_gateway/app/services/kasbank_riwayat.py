"""Kas & Bank standar SO (kontrak backend/docs/KONTRAK-kasbank-standar-so-20261109.md): bundel dokumen + riwayat untuk mutasi manual dan
transfer, riwayat rekening -- bentuk SAMA dengan modul lain (services/biaya_riwayat: _dok, ekor so_riwayat._selesaikan).

Sumber waktu/aktor (Law 34: hanya yang tercatat, tak ditebak):
- mutasi manual : dicatat = jurnal asli (created_at/created_by); batal = bank_transactions.voided_at/voided_by/void_reason, atau jurnal
                  pembalik (created_at/created_by) bila dibalik lewat pintu lain tanpa penanda baris.
- transfer      : dibuat/diposting/dibatalkan = kolom bank_transfers (created_*, posted_*, voided_*). Draf yang dihapus tak berjejak.
- rekening      : dibuat (created_*), saldo awal (jurnal BANK_OPENING_BALANCE/OPENING), penyesuaian/hitung fisik (jurnal ADJUSTMENT).
                  Perubahan data rekening (nama, nonaktif) TIDAK diaudit hari ini -> tidak tampil; jangan diklaim lengkap."""
from typing import Optional

from . import kasbank_daftar as KD
from .biaya_riwayat import _dok, _tgl
from .so_riwayat import _Kumpul, _rp, _selesaikan


async def _manual(conn, tid: str, tx_id):
    return await conn.fetchrow(KD.SQL_MANUAL + " AND bt.id = $2", tid, tx_id)


async def _pembalik(conn, tid: str, je_id):
    return await conn.fetchrow("SELECT id, journal_number, journal_date, total_debit, created_at, created_by FROM journal_entries "
                               "WHERE id = $1 AND tenant_id = $2", je_id, tid) if je_id else None


async def _jurnal(conn, tid: str, je_id):
    return await conn.fetchrow("SELECT id, journal_number, journal_date, total_debit, reversed_by_id FROM journal_entries "
                               "WHERE id = $1 AND tenant_id = $2", je_id, tid) if je_id else None


def _kel_jurnal(j, r, batal: bool) -> list:
    hasil = [_dok("journal", j["id"], j["journal_number"], j["journal_date"], KD.rp(j["total_debit"]), "diposting", batal)] if j else []
    if r:
        hasil.append(_dok("journal", r["id"], r["journal_number"], r["journal_date"], KD.rp(r["total_debit"]), "pembalik"))
    return hasil


# ---------------------------------------------------------------- mutasi manual
async def dokumen_manual(conn, tid: str, tx_id) -> Optional[dict]:
    """None = bukan mutasi manual sah di tenant ini (pemanggil -> 404)."""
    m = await _manual(conn, tid, tx_id)
    if not m:
        return None
    j, r = await _jurnal(conn, tid, m["journal_id"]), await _pembalik(conn, tid, m["reversed_by_id"])
    status = "dibatalkan" if m["batal"] else "tercatat"
    kel = [{"key": "mutasi", "label": "Mutasi",
            "docs": [_dok("bank_transaction", m["id"], m["nomor"], m["tgl"], KD.rp(abs(m["net"])), status, m["batal"])]}]
    jurnal = _kel_jurnal(j, r, m["batal"])
    if jurnal:
        kel.append({"key": "jurnal", "label": "Jurnal", "docs": jurnal})
    return {
        "anchor": {"kind": "bank_transaction", "id": str(m["id"]), "number": m["nomor"], "date": _tgl(m["tgl"]),
                   "position_text": KD.position_text_manual(m["net"], m["account_name"], m["batal"], m["void_reason"]),
                   "total": KD.rp(abs(m["net"])), "account_name": m["account_name"]},
        "default": {"group": "mutasi", "index": 0},
        "groups": kel,
    }


async def riwayat_manual(conn, tid: str, tx_id, limit: int = 200) -> Optional[dict]:
    m = await _manual(conn, tid, tx_id)
    if not m:
        return None
    n, masuk = m["nomor"], KD.arah(m["net"]) == "masuk"
    k = _Kumpul()
    jenis = "Uang masuk" if masuk else "Uang keluar"
    arah = f"ke {m['account_name']}" if masuk else f"dari {m['account_name']}"
    k.tambah(m["je_at"], "MUTASI_DICATAT", f"{jenis} {n} {_rp(abs(m['net']))} {arah} dicatat", m["je_by"], "bank_transaction", m["id"], n)
    if m["batal"]:
        r = await _pembalik(conn, tid, m["reversed_by_id"])
        at, aktor = (m["voided_at"], m["voided_by"]) if m["voided_at"] else ((r["created_at"], r["created_by"]) if r else (None, None))
        alasan = f": {m['void_reason']}" if m["void_reason"] else ""
        k.tambah(at, "MUTASI_DIBATALKAN", f"{jenis} {n} dibatalkan (void){alasan}", aktor, "bank_transaction", m["id"], n)
    keluar, total = await _selesaikan(conn, tid, k, {}, {}, limit)
    return {"transaction_id": str(m["id"]), "nomor": n, "events": keluar, "total": total, "omitted": []}


# ---------------------------------------------------------------- transfer
SQL_TRANSFER = """SELECT t.id, t.transfer_number, t.transfer_date, t.status, t.amount, t.fee_amount, t.total_amount, t.journal_id,
       t.created_at, t.created_by, t.posted_at, t.posted_by, t.voided_at, t.voided_by, t.voided_reason,
       fb.account_name AS asal, tb.account_name AS tujuan
FROM bank_transfers t
LEFT JOIN bank_accounts fb ON fb.id = t.from_bank_id AND fb.tenant_id = t.tenant_id
LEFT JOIN bank_accounts tb ON tb.id = t.to_bank_id AND tb.tenant_id = t.tenant_id
WHERE t.id = $1 AND t.tenant_id = $2"""


async def dokumen_transfer(conn, tid: str, transfer_id) -> Optional[dict]:
    t = await conn.fetchrow(SQL_TRANSFER, transfer_id, tid)
    if not t:
        return None
    batal = t["status"] == "void"
    j = await _jurnal(conn, tid, t["journal_id"])
    r = await _pembalik(conn, tid, j["reversed_by_id"]) if j else None
    kel = [{"key": "transfer", "label": "Transfer",
            "docs": [_dok("bank_transfer", t["id"], t["transfer_number"], t["transfer_date"], KD.rp(t["total_amount"]), t["status"], batal)]}]
    jurnal = _kel_jurnal(j, r, batal)
    if jurnal:
        kel.append({"key": "jurnal", "label": "Jurnal", "docs": jurnal})
    mutasi = await conn.fetch(
        """SELECT id, reference_type, reference_number, transaction_date, amount FROM bank_transactions
           WHERE tenant_id = $1 AND reference_id = $2 AND reference_type IN ('transfer', 'transfer_void')
           ORDER BY created_at, id""", tid, transfer_id)
    if mutasi:
        kel.append({"key": "mutasi", "label": "Mutasi bank", "docs": [
            _dok("bank_transaction", b["id"], b["reference_number"], b["transaction_date"], KD.rp(abs(b["amount"] or 0)),
                 "pembalik" if b["reference_type"] == "transfer_void" else ("dibatalkan" if batal else "tercatat"),
                 batal and b["reference_type"] == "transfer") for b in mutasi]})
    return {
        "anchor": {"kind": "bank_transfer", "id": str(t["id"]), "number": t["transfer_number"], "date": _tgl(t["transfer_date"]),
                   "position_text": KD.position_text_transfer(t["status"], t["asal"], t["tujuan"]),
                   "total": KD.rp(t["total_amount"]), "account_name": t["asal"]},
        "default": {"group": "transfer", "index": 0},
        "groups": kel,
    }


async def riwayat_transfer(conn, tid: str, transfer_id, limit: int = 200) -> Optional[dict]:
    t = await conn.fetchrow(SQL_TRANSFER, transfer_id, tid)
    if not t:
        return None
    n = t["transfer_number"]
    k = _Kumpul()
    k.tambah(t["created_at"], "TRANSFER_DIBUAT", f"Transfer {n} {_rp(t['amount'])} dari {t['asal']} ke {t['tujuan']} dibuat", t["created_by"],
             "bank_transfer", t["id"], n)
    k.tambah(t["posted_at"], "TRANSFER_DIPOSTING", f"Transfer {n} diposting", t["posted_by"], "bank_transfer", t["id"], n)
    alasan = f": {t['voided_reason']}" if t["voided_reason"] else ""
    k.tambah(t["voided_at"], "TRANSFER_DIBATALKAN", f"Transfer {n} dibatalkan (void){alasan}", t["voided_by"], "bank_transfer", t["id"], n)
    keluar, total = await _selesaikan(conn, tid, k, {}, {}, limit)
    return {"transfer_id": str(t["id"]), "transfer_number": n, "events": keluar, "total": total, "omitted": []}


# ---------------------------------------------------------------- rekening
async def riwayat_rekening(conn, tid: str, ba_id, limit: int = 200) -> Optional[dict]:
    ba = await conn.fetchrow("SELECT id, account_name, created_at, created_by FROM bank_accounts WHERE id = $1 AND tenant_id = $2", ba_id, tid)
    if not ba:
        return None
    nama = ba["account_name"]
    k = _Kumpul()
    k.tambah(ba["created_at"], "REKENING_DIBUAT", f"Rekening {nama} dibuat", ba["created_by"], "bank_account", ba["id"], nama)
    for j in await conn.fetch(
            """SELECT id, journal_number, source_type, total_debit, created_at, created_by, reversed_by_id FROM journal_entries
               WHERE tenant_id = $1 AND source_id = $2 AND source_type IN ('BANK_OPENING_BALANCE', 'OPENING', 'ADJUSTMENT')
               ORDER BY created_at, id""", tid, ba_id):
        awal = j["source_type"] != "ADJUSTMENT"
        teks = (f"Saldo awal {nama} {_rp(j['total_debit'])} dicatat" if awal
                else f"Penyesuaian saldo {nama} {_rp(j['total_debit'])} ({j['journal_number']})")
        k.tambah(j["created_at"], "SALDO_AWAL" if awal else "PENYESUAIAN_SALDO", teks + (" — dibalik" if j["reversed_by_id"] else ""),
                 j["created_by"], "journal", j["id"], j["journal_number"])
    keluar, total = await _selesaikan(conn, tid, k, {}, {}, limit)
    return {"bank_account_id": str(ba["id"]), "account_name": nama, "events": keluar, "total": total, "omitted": []}
