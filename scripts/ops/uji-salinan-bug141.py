"""BUG-141 / ROW (t) -- uji di SALINAN (klon milkydb): handler ASLI GET /bank-accounts/{id}/transactions per akun aktif.
T1 tanpa_batal: tak ada baris yang jurnalnya dibatalkan/pembalik (cek MANDIRI atas journal_entries); total == jumlah baris.
T2 yang disembunyikan = tepat baris batal + pembalik (selisih bawaan − tanpa_batal == baris ber-jurnal dibalik/pembalik), net nol.
T3 saldo berjalan baris TERBARU sama dengan bawaan (pasangan net nol) -- kolom Saldo terbaca."""
import asyncio
import os
import sys
from decimal import Decimal as D

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bank_accounts as RB  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
SQL_BATAL = """SELECT bt.id FROM bank_transactions bt JOIN journal_entries je ON je.id = bt.journal_id AND je.tenant_id = $1
               WHERE bt.tenant_id = $1 AND bt.bank_account_id = $2
                 AND (je.reversed_by_id IS NOT NULL OR je.reversal_of_id IS NOT NULL OR je.source_type = 'REVERSAL' OR je.source_type LIKE '%\\_REVERSAL')"""


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug141"}
    return r


async def semua(tid, aid, tb):
    out, skip = [], 0
    while True:
        d = await RB.get_bank_transactions(req(tid), aid, transaction_type=None, date_from=None, date_to=None, is_reconciled=None,
                                           skip=skip, limit=200, tanpa_batal=tb)
        out += d["items"]
        skip += 200
        if not d["has_more"]:
            return d["total"], out


async def utama():
    pool = await RB.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b141_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            akun = await conn.fetch("SELECT id, account_name FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY account_name", tid)
            for a in akun:
                batal = {str(r["id"]) for r in await conn.fetch(SQL_BATAL, tid, a["id"])}
                n0, b0 = await semua(tid, a["id"], False)
                n1, b1 = await semua(tid, a["id"], True)
                nama = f"{tid} {a['account_name']}"
                id1 = {x["id"] for x in b1}
                cek(f"{nama} T1 tanpa baris batal/pembalik; total == baris", not (id1 & batal) and n1 == len(b1), f"{n1} baris, {len(batal)} batal")
                hilang = {x["id"] for x in b0} - id1
                net = sum((D(str(x["amount"])) for x in b0 if x["id"] in hilang), D(0))
                cek(f"{nama} T2 disembunyikan == batal+pembalik, net nol", hilang == batal and net == 0, f"hilang {len(hilang)} net {net}")
                if b0 and b1:
                    cek(f"{nama} T3 saldo berjalan terbaru sama", D(str(b0[0]["running_balance"])) == D(str(b1[0]["running_balance"]))
                        if b0[0]["id"] == b1[0]["id"] else True, f"{b0[0]['running_balance']} vs {b1[0]['running_balance']}")
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return all(HASIL)

sys.exit(0 if asyncio.run(utama()) else 1)
