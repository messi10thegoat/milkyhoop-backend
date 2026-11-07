"""Biaya standar SO tahap 1 -- uji di SALINAN (klon milkydb): handler ASLI list_expenses / get_expenses_summary / create_expense.
T1 per tenant (kaos, nyawit) x kunci: summary {k}_count == daftar ?aksi=k total; {k}_value == daftar sum_total; tiap baris daftar
   ber-aksi_semua memuat k (draf/tanpa_bukti) + kontrol: kunci tanpa_bukti benar-benar membedakan (posted ber-bukti tak ikut).
T2 with_totals tanpa saring == Σ total_amount non-void (SQL langsung).
T3 idempotensi (nyawit, tulis di salinan): kunci sama + isi sama -> replay id SAMA, biaya +1 saja; isi beda -> 409; tanpa kunci -> +1 (opsional)."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.schemas.expenses import CreateExpenseRequest  # noqa: E402

HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None, metode="GET"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": metode, "path": "/api/expenses", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan-biaya"}
    return r


async def daftar(tid, uid, **kw):
    a = dict(skip=0, limit=100, status="all", vendor_id=None, account_id=None, date_from=None, date_to=None, search=None,
             sort_by="expense_date", sort_order="desc", is_billable=None, billing_status=None, aksi=None, with_totals=True)
    a.update(kw)
    return S.ExpenseListResponse.model_validate(await EX.list_expenses(req(tid, uid), **a)).model_dump(mode="json")


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        uid = {t: await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', t)
               for t in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")}
    for tid, u in uid.items():
        ring = S.ExpenseSummaryResponse.model_validate(await EX.get_expenses_summary(req(tid, u), period="month", search=None)).model_dump(mode="json")["data"]
        for k in ("draf", "tanpa_bukti", "bulan_ini"):
            d = await daftar(tid, u, aksi=k)
            cek(f"{tid} {k}: summary count == daftar total", ring[f"{k}_count"] == d["total"], f"{ring[f'{k}_count']} vs {d['total']}")
            cek(f"{tid} {k}: summary value == daftar sum_total", abs(ring[f"{k}_value"] - d["sum_total"]) < 0.005, f"{ring[f'{k}_value']} vs {d['sum_total']}")
            if k != "bulan_ini":
                cek(f"{tid} {k}: tiap baris aksi_semua memuat {k}", all(k in b["aksi_semua"] for b in d["items"]), str(len(d["items"])))
        semua = await daftar(tid, u)
        async with pool.acquire() as c:
            n = await c.fetchval("SELECT COALESCE(SUM(total_amount),0) FROM expenses WHERE tenant_id=$1 AND status<>'void'", tid)
            berbukti = await c.fetchval("""SELECT count(*) FROM expenses e WHERE e.tenant_id=$1 AND e.status='posted'
                                           AND EXISTS (SELECT 1 FROM expense_attachments a WHERE a.expense_id=e.id)""", tid)
        cek(f"{tid} with_totals tanpa saring == Σ non-void SQL", abs(semua["sum_total"] - float(n)) < 0.005, f"{semua['sum_total']} vs {n}")
        cek(f"{tid} kontrol: posted ber-bukti TIDAK ber-aksi tanpa_bukti",
            all("tanpa_bukti" not in b["aksi_semua"] for b in semua["items"] if b["status"] == "posted" and b["attachment_count"] > 0),
            f"posted berbukti={berbukti}")
        print(f"[info] {tid} contoh position_text: {sorted({b['position_text'] for b in semua['items']})[:4]}", flush=True)

    # T3 idempotensi (nyawit)
    tid, u = "pt-kita-nyawit-bersama", uid["pt-kita-nyawit-bersama"]
    async with pool.acquire() as c:
        kas = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active AND account_type IN ('cash','petty_cash') ORDER BY created_at LIMIT 1", tid)
        beban = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_type='EXPENSE' AND NOT is_header ORDER BY account_code LIMIT 1", tid)
        hari = await c.fetchval("SELECT tanggal_bisnis($1)", tid)
    badan = dict(expense_date=hari, paid_through_id=kas, account_id=beban, amount=1000, notes="UJI biaya t1")

    async def jumlah():
        async with pool.acquire() as c:
            return await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id=$1", tid)
    kunci = f"uji-{uuid.uuid4().hex[:8]}"
    n0 = await jumlah()
    r1 = Response(); a = await EX.create_expense(req(tid, u, kunci, "POST"), CreateExpenseRequest(**badan), r1)
    r2 = Response(); b = await EX.create_expense(req(tid, u, kunci, "POST"), CreateExpenseRequest(**badan), r2)
    n1 = await jumlah()
    cek("idem: ulang kunci+isi sama -> replay id SAMA, biaya +1", a["data"]["id"] == b["data"]["id"] and n1 == n0 + 1 and r2.headers.get("X-Idempotent-Replay") == "true",
        f"+{n1 - n0} replay={r2.headers.get('X-Idempotent-Replay')}")
    try:
        await EX.create_expense(req(tid, u, kunci, "POST"), CreateExpenseRequest(**{**badan, "amount": 2000}), Response())
        cek("idem: kunci sama isi beda -> 409", False, "lolos")
    except HTTPException as e:
        cek("idem: kunci sama isi beda -> 409", e.status_code == 409 and e.detail.get("code") == "IDEMPOTENCY_KEY_REUSED", str(e.detail)[:80])
    await EX.create_expense(req(tid, u, None, "POST"), CreateExpenseRequest(**badan), Response())
    cek("idem opsional: tanpa kunci -> biaya baru", await jumlah() == n1 + 1)

    # T4 (Law 33: T1 kosong-isi untuk draf & posted ber-bukti di data nyata) -- benih DI SALINAN: 1 draf + 1 lampiran pada biaya posted
    ring0 = S.ExpenseSummaryResponse.model_validate(await EX.get_expenses_summary(req(tid, u), period="month", search=None)).model_dump(mode="json")["data"]
    await EX.create_expense(req(tid, u, None, "POST"), CreateExpenseRequest(**badan, status="draft"), Response())
    async with pool.acquire() as c:
        pid = await c.fetchval("SELECT id FROM expenses WHERE tenant_id=$1 AND status='posted' ORDER BY created_at LIMIT 1", tid)
        await c.execute("""INSERT INTO expense_attachments (id, tenant_id, expense_id, file_name, file_path, file_size, mime_type, uploaded_at, uploaded_by)
                            VALUES (gen_random_uuid(), $1, $2, 'uji.jpg', 'uji/uji.jpg', 10, 'image/jpeg', NOW(), $3)""", tid, pid, u)
    ring1 = S.ExpenseSummaryResponse.model_validate(await EX.get_expenses_summary(req(tid, u), period="month", search=None)).model_dump(mode="json")["data"]
    cek("benih: draf +1 di summary", ring1["draf_count"] == ring0["draf_count"] + 1, f"{ring0['draf_count']} -> {ring1['draf_count']}")
    cek("benih: lampiran -> tanpa_bukti -1", ring1["tanpa_bukti_count"] == ring0["tanpa_bukti_count"] - 1, f"{ring0['tanpa_bukti_count']} -> {ring1['tanpa_bukti_count']}")
    dd = await daftar(tid, u, aksi="draf")
    cek("benih: daftar ?aksi=draf == summary, baris Draf", dd["total"] == ring1["draf_count"] and all(b["position_text"] == "Draf" and b["aksi"] == "draf" for b in dd["items"]), str(dd["total"]))
    semua = await daftar(tid, u)
    b = next(x for x in semua["items"] if x["id"] == str(pid))
    cek("benih: posted ber-bukti -> tanpa aksi, posisi tanpa 'tanpa bukti', dokumen memuat attachment",
        b["aksi_semua"] == [] and "tanpa bukti" not in b["position_text"] and {"type": "attachment"} in b["documents"], f"{b['position_text']} {b['documents']}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
