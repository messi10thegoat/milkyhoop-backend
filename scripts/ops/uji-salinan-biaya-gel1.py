"""Biaya BE gelombang 1 uji DB di SALINAN (bukan prod): handler ASLI atas klon milkydb, tenant nyawit + kaos. Mutasi HANYA di salinan.
G1 daftar: biaya_keadaan/status_text == penentu, billed_to_customer_name == customers (tersaring tenant), total == DB · G2 detail: keadaan/position_text == daftar, vendor objek
· G3 documents: anchor keadaan == detail · G4 summary: cakupan + period_label, cacah pill == DB · G5 void/preview TANPA alasan: 200, can_proceed false, blok BIAYA_REASON_REQUIRED
SAJA, jurnal pembalik tetap terlihat == pratinjau berkalimat, NOL tulis · G6 delete/preview: draf boleh, terbit/void/404/lintas tenant ditolak berkode, DELETE memakai penentu sama
· G7 idempotensi opsional post/void/PATCH: kunci sama = replay (nol tulisan ganda), isi beda = 409."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.services import biaya_daftar as BD  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, path, method="GET", kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": path, "headers": h})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def hitung(c, tid):
    return (await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id = $1", tid))


async def kode(coro):
    try:
        await coro
    except GalatBerkode as e:
        return (e.status_code, e.code)
    except HTTPException as e:
        return (e.status_code, (e.detail.get("code") if isinstance(e.detail, dict) else None))
    return (200, None)


async def salin_draf(c, tid, sumber, nomor):
    did = uuid.uuid4()
    await c.execute("""INSERT INTO expenses (id, tenant_id, expense_number, expense_date, paid_through_id, paid_through_name,
                           paid_through_coa_id, vendor_id, vendor_name, account_id, account_name, currency, subtotal, tax_id, tax_name,
                           tax_rate, tax_amount, pph_type, pph_rate, pph_amount, total_amount, is_itemized, status, notes, created_by)
                       SELECT $1, tenant_id, $4, CURRENT_DATE, paid_through_id, paid_through_name, paid_through_coa_id, vendor_id,
                           vendor_name, account_id, account_name, currency, subtotal, tax_id, tax_name, tax_rate, tax_amount, pph_type,
                           pph_rate, pph_amount, total_amount, false, 'draft', 'uji salinan gel1', created_by
                       FROM expenses WHERE id = $2 AND tenant_id = $3""", did, sumber, tid, nomor)
    return did


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        # ---------------- G1 + G2 + G3 per tenant
        for tid in (NY, KA):
            r = await req(c, tid, "/api/expenses")
            hasil = await EX.list_expenses(r, skip=0, limit=100, status="all", vendor_id=None, account_id=None, date_from=None, date_to=None, search=None,
                                           sort_by="expense_date", sort_order="desc", is_billable=None, billing_status=None, aksi=None, with_totals=False)
            lst = S.ExpenseListResponse.model_validate(hasil).model_dump(mode="json")
            n_db = await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id = $1", tid)
            cek(f"G1 {tid} daftar: total == DB ({n_db})", lst["total"] == n_db)
            salah = []
            for it in lst["items"]:
                row = await c.fetchrow("SELECT status, total_amount, billed_to_customer_id FROM expenses WHERE id = $1 AND tenant_id = $2", it["id"], tid)
                nama = await c.fetchval("SELECT name FROM customers WHERE id = $1 AND tenant_id = $2", row["billed_to_customer_id"], tid) if row["billed_to_customer_id"] else None
                if (it["biaya_keadaan"], it["status_text"]) != (BD.keadaan(row["status"]), BD.status_text(row["status"])) or it["billed_to_customer_name"] != nama \
                        or it["total_amount"] != row["total_amount"]:
                    salah.append((it["id"], it["biaya_keadaan"], it["status_text"], it["billed_to_customer_name"], nama))
            cek(f"G1 {tid} tiap baris: keadaan + status_text + nama pelanggan + total == DB ({len(lst['items'])} baris)", not salah, str(salah[:3]))
            semua_status = {it["status"] for it in lst["items"]}
            cek(f"G1 {tid} status dikenal -> keadaan terisi (tak ada None)", all(it["biaya_keadaan"] for it in lst["items"]), str(semua_status))
            beda_detail = []
            for it in lst["items"][:8]:
                rr = await req(c, tid, f"/api/expenses/{it['id']}")
                d = S.ExpenseDetailResponse.model_validate(await EX.get_expense_detail(rr, uuid.UUID(it["id"]))).model_dump(mode="json")["data"]
                if (d["biaya_keadaan"], d["status_text"], d["position_text"]) != (it["biaya_keadaan"], it["status_text"], it["position_text"]):
                    beda_detail.append(("kel/pos", it["id"], d["position_text"], it["position_text"]))
                vend = d["vendor"]
                if (vend is None) != (d["vendor_id"] is None) or (vend and (vend["id"] != str(d["vendor_id"]) or vend["name"] != d["vendor_name"])):
                    beda_detail.append(("vendor", it["id"], vend))
                if d["billed_to_customer_name"] != it["billed_to_customer_name"]:
                    beda_detail.append(("pelanggan", it["id"]))
                rd = await req(c, tid, f"/api/expenses/{it['id']}/documents")
                an = S.ExpenseDocumentsResponse.model_validate(await EX.get_expense_documents(rd, uuid.UUID(it["id"]))).model_dump(mode="json")["data"]["anchor"]
                if (an["biaya_keadaan"], an["status_text"]) != (d["biaya_keadaan"], d["status_text"]):
                    beda_detail.append(("anchor", it["id"], an["biaya_keadaan"]))
            cek(f"G2/G3 {tid} detail == daftar (keadaan, position_text, vendor objek, pelanggan) dan anchor == detail ({min(8, len(lst['items']))} biaya)", not beda_detail,
                str(beda_detail[:3]))
            # G4 summary
            for per in ("week", "month", "quarter", "year"):
                rs = await req(c, tid, "/api/expenses/summary")
                sm = S.ExpenseSummaryResponse.model_validate(await EX.get_expenses_summary(rs, period=per, search=None)).model_dump(mode="json")["data"]
                draf_db = await c.fetchval("SELECT count(*) FROM expenses WHERE tenant_id = $1 AND status = 'draft'", tid)
                cek(f"G4 {tid} summary {per}: cakupan+label server, draf_count == DB (makna tak berubah)",
                    sm["cakupan"] == BD.cakupan(per) and sm["period_label"] == BD.period_label(per) and sm["draf_count"] == draf_db, f"{sm['cakupan']} {sm['draf_count']} vs {draf_db}")

        # ---------------- G5 void/preview tanpa alasan (nyawit, biaya terbit berjurnal)
        e = await c.fetchrow("""SELECT id FROM expenses WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL AND NOT is_itemized
                                ORDER BY created_at DESC LIMIT 1""", NY)
        cek("prasyarat: nyawit punya biaya terbit berjurnal", e is not None)
        eid = e["id"]
        sebelum = await hitung(c, NY)
        r = await req(c, NY, "/x", "POST")
        tanpa = (await EX.preview_void_expense(r, eid, None))["data"]
        r = await req(c, NY, "/x", "POST")
        dengan = (await EX.preview_void_expense(r, eid, S.VoidExpensePreviewRequest(reason="uji gel1")))["data"]
        cek("G5 tanpa alasan: 200, can_proceed false, HANYA blok BIAYA_REASON_REQUIRED", tanpa["can_proceed"] is False and [b["code"] for b in tanpa["blocks"]] == ["BIAYA_REASON_REQUIRED"],
            str(tanpa["blocks"]))
        cek("G5 dengan alasan: can_proceed true, tanpa blok", dengan["can_proceed"] is True and dengan["blocks"] == [], str(dengan["blocks"]))
        jt = [(x["account_code"], x["debit"], x["credit"]) for j in tanpa["journals"] for x in j["lines"]]
        jd = [(x["account_code"], x["debit"], x["credit"]) for j in dengan["journals"] for x in j["lines"]]
        cek("G5 jurnal pembalik terlihat dan SAMA dgn pratinjau berkalimat", jt and jt == jd, f"{jt} vs {jd}")
        cek("G5 NOL tulis", await hitung(c, NY) == sebelum and await c.fetchval("SELECT status FROM expenses WHERE id = $1 AND tenant_id = $2", eid, NY) == "posted")

        # ---------------- G6 delete/preview
        did = await salin_draf(c, NY, eid, "EXP-UJI-G6")
        sebelum = await hitung(c, NY)
        dp = (await EX.preview_delete_expense(await req(c, NY, "/x", "POST"), did))["data"]
        cek("G6 draf: can_proceed true, tanpa blok, NOL tulis", dp["can_proceed"] and dp["blocks"] == [] and dp["journals"] == [] and await hitung(c, NY) == sebelum, str(dp))
        tp = (await EX.preview_delete_expense(await req(c, NY, "/x", "POST"), eid))["data"]
        cek("G6 biaya terbit: BIAYA_BUKAN_DRAF", tp["can_proceed"] is False and [b["code"] for b in tp["blocks"]] == ["BIAYA_BUKAN_DRAF"], str(tp))
        acak = uuid.uuid4()
        t404 = (await EX.preview_delete_expense(await req(c, NY, "/x", "POST"), acak))["data"]
        tl = (await EX.preview_delete_expense(await req(c, KA, "/x", "POST"), did))["data"]
        cek("G6 id acak dan LINTAS TENANT (id nyawit, tenant kaos): EXPENSE_TIDAK_DITEMUKAN", [b["code"] for b in t404["blocks"]] == ["EXPENSE_TIDAK_DITEMUKAN"]
            and [b["code"] for b in tl["blocks"]] == ["EXPENSE_TIDAK_DITEMUKAN"], f"{t404['blocks']} {tl['blocks']}")
        cek("G6 DELETE biaya terbit ditolak berkode, baris tetap", await kode(EX.delete_expense(await req(c, NY, "/x", "DELETE"), eid)) == (400, "BIAYA_BUKAN_DRAF")
            and await c.fetchval("SELECT count(*) FROM expenses WHERE id = $1 AND tenant_id = $2", eid, NY) == 1)
        await EX.delete_expense(await req(c, NY, "/x", "DELETE"), did)
        cek("G6 DELETE draf berhasil (penentu sama dgn pratinjau)", await c.fetchval("SELECT count(*) FROM expenses WHERE id = $1 AND tenant_id = $2", did, NY) == 0)

        # ---------------- G7 idempotensi opsional
        d1 = await salin_draf(c, NY, eid, "EXP-UJI-G7A")
        resp1, resp2 = Response(), Response()
        k = f"uji-gel1-{uuid.uuid4()}"
        a = await EX.post_expense(await req(c, NY, "/x", "POST", k), d1, resp1)
        j_sesudah_a = await hitung(c, NY)
        b = await EX.post_expense(await req(c, NY, "/x", "POST", k), d1, resp2)
        cek("G7 post kunci sama: respons sama, replay, nol jurnal/bank ganda", a == b and resp2.headers.get("x-idempotent-replay") == "true" and await hitung(c, NY) == j_sesudah_a,
            f"{resp2.headers}")
        kv = f"uji-gel1v-{uuid.uuid4()}"
        v1 = await EX.void_expense(await req(c, NY, "/x", "POST", kv), d1, S.VoidExpenseRequest(reason="uji idem"), Response())
        h_sesudah_v = await hitung(c, NY)
        resp3 = Response()
        v2 = await EX.void_expense(await req(c, NY, "/x", "POST", kv), d1, S.VoidExpenseRequest(reason="uji idem"), resp3)
        cek("G7 void kunci sama: replay, nol pembalik ganda", v1 == v2 and resp3.headers.get("x-idempotent-replay") == "true" and await hitung(c, NY) == h_sesudah_v)
        kd = await kode(EX.void_expense(await req(c, NY, "/x", "POST", kv), d1, S.VoidExpenseRequest(reason="isi berbeda"), Response()))
        cek("G7 void kunci sama + isi beda: 409 IDEMPOTENCY_KEY_REUSED", kd == (409, "IDEMPOTENCY_KEY_REUSED"), str(kd))
        d2 = await salin_draf(c, NY, eid, "EXP-UJI-G7B")
        ku = f"uji-gel1u-{uuid.uuid4()}"
        u1 = await EX.update_expense(await req(c, NY, "/x", "PATCH", ku), d2, S.UpdateExpenseRequest(notes="uji patch idem"), Response())
        resp4 = Response()
        u2 = await EX.update_expense(await req(c, NY, "/x", "PATCH", ku), d2, S.UpdateExpenseRequest(notes="uji patch idem"), resp4)
        ku2 = await kode(EX.update_expense(await req(c, NY, "/x", "PATCH", ku), d2, S.UpdateExpenseRequest(notes="beda"), Response()))
        cek("G7 PATCH kunci sama: replay; isi beda 409", u1 == u2 and resp4.headers.get("x-idempotent-replay") == "true" and ku2 == (409, "IDEMPOTENCY_KEY_REUSED"), f"{ku2}")
        # tanpa kunci = perilaku lama (tanpa replay): dua PATCH berbeda sama-sama jalan
        u3 = await EX.update_expense(await req(c, NY, "/x", "PATCH"), d2, S.UpdateExpenseRequest(notes="tanpa kunci"), Response())
        cek("G7 tanpa kunci = perilaku lama", u3["data"]["notes"] == "tanpa kunci")

    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
