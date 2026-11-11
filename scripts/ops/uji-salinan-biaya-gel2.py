"""Biaya BE gelombang 2 uji DB di SALINAN (bukan prod): handler ASLI atas klon milkydb, tenant nyawit + kaos. Mutasi HANYA di salinan.
H1 pratinjau BUAT posted == tulis nyata (jurnal baris, mutasi bank, angka) + NOL tulis (jurnal/bank/biaya/urut nomor) · H2 draft: tanpa jurnal, angka == baris draf nyata · H3 itemized +
pajak + PPh · H4 ditolak (rekening tak ada): 200, blok, nol tulis, galat tulis nyata SAMA · H5 saldo minus: pratinjau == tulis (bisa lanjut atau ditolak, kode sama) · H6 pratinjau UBAH:
sebelum == DB, sesudah == baris sesudah PATCH nyata, nol tulis · H7 UBAH ditolak: terbit (BIAYA_BUKAN_DRAF), id acak + lintas tenant (EXPENSE_TIDAK_DITEMUKAN)."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, path="/x", method="POST"):
    r = Request({"type": "http", "method": method, "path": path, "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def hitung(c, tid):
    return (await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT COALESCE(max(last_number), 0) FROM expense_sequences WHERE tenant_id = $1", tid))


async def galat(coro):
    try:
        await coro
    except GalatBerkode as e:
        return (e.status_code, e.code)
    except HTTPException as e:
        return (e.status_code, (e.detail.get("code") if isinstance(e.detail, dict) else str(e.detail)))
    return None


async def efek_nyata(c, tid, expense_id):
    row = await c.fetchrow("SELECT * FROM expenses WHERE id = $1 AND tenant_id = $2", expense_id, tid)
    jl = []
    if row["journal_id"]:
        jl = [(r["account_code"], float(r["debit"] or 0), float(r["credit"] or 0)) for r in await c.fetch(
            """SELECT co.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts co ON co.id = jl.account_id
               JOIN journal_entries je ON je.id = jl.journal_id WHERE je.id = $1 AND je.tenant_id = $2 ORDER BY jl.line_number""", row["journal_id"], tid)]
    bank = [(float(r["amount"]), r["transaction_type"]) for r in await c.fetch(
        "SELECT amount, transaction_type FROM bank_transactions WHERE tenant_id = $1 AND journal_id = $2", tid, row["journal_id"])] if row["journal_id"] else []
    return row, jl, bank


def baris_pratinjau(p):
    return [(x["account_code"], x["debit"], x["credit"]) for j in p["journals"] for x in j["lines"]]


async def salin_draf(c, tid, sumber, nomor):
    did = uuid.uuid4()
    await c.execute("""INSERT INTO expenses (id, tenant_id, expense_number, expense_date, paid_through_id, paid_through_name,
                           paid_through_coa_id, vendor_id, vendor_name, account_id, account_name, currency, subtotal, tax_id, tax_name,
                           tax_rate, tax_amount, pph_type, pph_rate, pph_amount, total_amount, is_itemized, status, notes, created_by)
                       SELECT $1, tenant_id, $4, CURRENT_DATE, paid_through_id, paid_through_name, paid_through_coa_id, vendor_id,
                           vendor_name, account_id, account_name, currency, subtotal, tax_id, tax_name, tax_rate, tax_amount, pph_type,
                           pph_rate, pph_amount, total_amount, false, 'draft', 'uji salinan gel2', created_by
                       FROM expenses WHERE id = $2 AND tenant_id = $3""", did, sumber, tid, nomor)
    return did


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        for tid in (NY, KA):
            e = await c.fetchrow("""SELECT * FROM expenses WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL AND NOT is_itemized
                                    ORDER BY created_at DESC LIMIT 1""", tid)
            cek(f"prasyarat {tid}: ada biaya terbit non-itemized sebagai templat", e is not None)
            if e is None:
                continue
            hari = (await c.fetchval("SELECT CURRENT_DATE"))
            dasar = dict(expense_date=hari, paid_through_id=e["paid_through_id"], account_id=e["account_id"], amount=100000, tax_rate=11, pph_rate=2)

            # H1 buat posted: pratinjau == tulis nyata
            b = S.CreateExpenseRequest(**dasar, status="posted")
            sebelum = await hitung(c, tid)
            p = (await EX.preview_create_expense(await req(c, tid), b))["data"]
            cek(f"H1 {tid} pratinjau buat posted: can_proceed, jurnal + bank", p["can_proceed"] and p["journals"] and p["bank_transactions"] and p["blocks"] == [], str(p)[:240])
            cek(f"H1 {tid} pratinjau NOL tulis (jurnal, bank, biaya, urut nomor ikut batal)", await hitung(c, tid) == sebelum, f"{sebelum} -> {await hitung(c, tid)}")
            nyata = await EX.create_expense(await req(c, tid), b)
            row, jl, bank = await efek_nyata(c, tid, str(nyata["data"]["id"]))
            cek(f"H1 {tid} jurnal pratinjau == jurnal tulis nyata", baris_pratinjau(p) == jl and len(jl) >= 2, f"{baris_pratinjau(p)} vs {jl}")
            cek(f"H1 {tid} mutasi bank pratinjau == nyata", [(x["amount"], x["transaction_type"]) for x in p["bank_transactions"]] == bank, f"{p['bank_transactions']} vs {bank}")
            cek(f"H1 {tid} angka pratinjau == baris nyata", all(Decimal(str(p["calculation"][k])) == Decimal(str(row[k])) for k in ("subtotal", "tax_amount", "pph_amount", "total_amount")),
                f"{p['calculation']} vs {[row[k] for k in ('subtotal', 'tax_amount', 'pph_amount', 'total_amount')]}")
            # H2 draft
            bd = S.CreateExpenseRequest(**dasar, status="draft")
            sebelum = await hitung(c, tid)
            pd = (await EX.preview_create_expense(await req(c, tid), bd))["data"]
            cek(f"H2 {tid} pratinjau draft: tanpa jurnal/bank, NOL tulis", pd["can_proceed"] and pd["journals"] == [] and pd["bank_transactions"] == [] and await hitung(c, tid) == sebelum)
            nd = await EX.create_expense(await req(c, tid), bd)
            rd, jd, bkd = await efek_nyata(c, tid, str(nd["data"]["id"]))
            cek(f"H2 {tid} angka draft == baris draf nyata, tanpa jurnal nyata", jd == [] and all(Decimal(str(pd["calculation"][k])) == Decimal(str(rd[k])) for k in ("subtotal", "tax_amount", "pph_amount", "total_amount")))
            # H3 itemized + pajak + PPh
            bi = S.CreateExpenseRequest(expense_date=hari, paid_through_id=e["paid_through_id"], amount=1, is_itemized=True, tax_rate=11, pph_rate=2, status="posted",
                                        line_items=[S.ExpenseItemRequest(account_id=e["account_id"], amount=30000), S.ExpenseItemRequest(account_id=e["account_id"], amount=20000)])
            pi = (await EX.preview_create_expense(await req(c, tid), bi))["data"]
            ni = await EX.create_expense(await req(c, tid), bi)
            ri, ji, bki = await efek_nyata(c, tid, str(ni["data"]["id"]))
            cek(f"H3 {tid} itemized+pajak+PPh: jurnal + angka pratinjau == nyata", baris_pratinjau(pi) == ji and all(Decimal(str(pi["calculation"][k])) == Decimal(str(ri[k]))
                for k in ("subtotal", "tax_amount", "pph_amount", "total_amount")) and Decimal(str(pi["calculation"]["subtotal"])) == 50000, f"{baris_pratinjau(pi)} vs {ji}")
            # H4 ditolak
            salah = S.CreateExpenseRequest(**{**dasar, "paid_through_id": uuid.uuid4()}, status="posted")
            sebelum = await hitung(c, tid)
            ps = (await EX.preview_create_expense(await req(c, tid), salah))["data"]
            g = await galat(EX.create_expense(await req(c, tid), salah))
            cek(f"H4 {tid} ditolak: 200, blok, nol tulis; galat tulis nyata SAMA", ps["can_proceed"] is False and len(ps["blocks"]) == 1 and g is not None and g[0] == 400
                and await hitung(c, tid) == sebelum and ps["blocks"][0]["message"] in str(g), f"{ps['blocks']} vs {g}")
            # H5 saldo minus: pratinjau == tulis
            besar = S.CreateExpenseRequest(**{**dasar, "amount": 9_000_000_000_000}, status="posted")
            sebelum = await hitung(c, tid)
            pb = (await EX.preview_create_expense(await req(c, tid), besar))["data"]
            ok_sesudah_preview = await hitung(c, tid) == sebelum
            gb = await galat(EX.create_expense(await req(c, tid), besar))
            cek(f"H5 {tid} jumlah sangat besar: pratinjau == tulis (lanjut/ditolak sama), pratinjau nol tulis", ok_sesudah_preview and pb["can_proceed"] == (gb is None)
                and (gb is None or pb["blocks"][0]["code"] in str(gb) or pb["blocks"][0]["message"] in str(gb)), f"{pb['can_proceed']} {pb['blocks']} vs {gb}")

            # H6 pratinjau UBAH
            did = await salin_draf(c, tid, e["id"], "EXP-UJI-G2U")
            row0 = await c.fetchrow("SELECT * FROM expenses WHERE id = $1 AND tenant_id = $2", did, tid)
            bu = S.UpdateExpenseRequest(amount=50000, tax_rate=11, pph_rate=2)
            pu = (await EX.preview_update_expense(await req(c, tid), did, bu))["data"]
            row_sesudah_preview = await c.fetchrow("SELECT * FROM expenses WHERE id = $1 AND tenant_id = $2", did, tid)
            cek(f"H6 {tid} pratinjau ubah: NOL tulis (baris tak berubah), sebelum == DB", dict(row0) == dict(row_sesudah_preview) and Decimal(str(pu["sebelum"]["total_amount"])) == Decimal(str(row0["total_amount"])),
                str(pu)[:240])
            await EX.update_expense(await req(c, tid, method="PATCH"), did, bu)
            row1 = await c.fetchrow("SELECT * FROM expenses WHERE id = $1 AND tenant_id = $2", did, tid)
            cek(f"H6 {tid} angka sesudah (pratinjau) == baris sesudah PATCH nyata", pu["can_proceed"] and pu["journals"] == [] and all(
                Decimal(str(pu["sesudah"][k])) == Decimal(str(row1[k])) for k in ("subtotal", "tax_amount", "pph_amount", "total_amount")) and pu["calculation"]["subtotal"] == 50000)
            # H7 ubah ditolak
            pt = (await EX.preview_update_expense(await req(c, tid), e["id"], bu))["data"]
            cek(f"H7 {tid} ubah biaya terbit: BIAYA_BUKAN_DRAF, sebelum/sesudah kosong", pt["can_proceed"] is False and [x["code"] for x in pt["blocks"]] == ["BIAYA_BUKAN_DRAF"] and pt["sesudah"] is None)
            pa = (await EX.preview_update_expense(await req(c, tid), uuid.uuid4(), bu))["data"]
            lain = KA if tid == NY else NY
            pl = (await EX.preview_update_expense(await req(c, lain), did, bu))["data"]
            cek(f"H7 {tid} id acak + LINTAS TENANT: EXPENSE_TIDAK_DITEMUKAN", [x["code"] for x in pa["blocks"]] == ["EXPENSE_TIDAK_DITEMUKAN"] and [x["code"] for x in pl["blocks"]] == ["EXPENSE_TIDAK_DITEMUKAN"],
                f"{pa['blocks']} {pl['blocks']}")

    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
