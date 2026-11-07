"""Biaya t2 uji DB di SALINAN (bukan prod): handler ASLI atas klon milkydb, tenant nyawit + kaos. Mutasi HANYA di salinan.
T1 detail aksi_tersedia (biaya terbit) bertipe · T2 documents: anchor.position_text == biaya_daftar.position_text, jurnal = expenses.journal_id
· T3 history: dibuat + diposting · T4 pratinjau terbitkan draf: can_proceed, jurnal EXPENSE seimbang, NOL tulis (jurnal/bank/status) ·
T5 pratinjau batalkan terbit: jurnal pembalik, NOL tulis · T6 batalkan NYATA (salinan): history alasan, documents pembalik, aksi batal ·
T7 404 id acak + LINTAS TENANT (id nyawit dgn tenant kaos) untuk documents/history."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.services import biaya_daftar as BD  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, path, method="GET"):
    r = Request({"type": "http", "method": method, "path": path, "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def hitung(c, tid):
    return (await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", tid),
            await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", tid))


async def kode404(coro):
    try:
        await coro
    except HTTPException as e:
        return e.status_code
    return 200


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        e = await c.fetchrow("""SELECT * FROM expenses WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL
                                AND NOT is_itemized ORDER BY created_at DESC LIMIT 1""", NY)
        cek("prasyarat: nyawit punya biaya terbit non-itemized berjurnal", e is not None)
        eid = e["id"]

        r = await req(c, NY, f"/api/expenses/{eid}")
        d = S.ExpenseDetailResponse.model_validate(await EX.get_expense_detail(r, eid)).model_dump(mode="json")
        a = d["aksi_tersedia"]
        cek("T1 detail aksi_tersedia satu bentuk, posting tak tersedia",
            set(a) == {"posting", "ubah", "hapus", "batalkan", "tambah_bukti"} and a["posting"]["tersedia"] is False
            and set(a["batalkan"]) == {"tersedia", "alasan"}, str(a))

        r = await req(c, NY, f"/api/expenses/{eid}/documents")
        dok = S.ExpenseDocumentsResponse.model_validate(await EX.get_expense_documents(r, eid)).model_dump(mode="json")["data"]
        ada = await c.fetchval("SELECT EXISTS(SELECT 1 FROM expense_attachments WHERE expense_id = $1 AND tenant_id = $2)", eid, NY)
        harap = BD.position_text(e["status"], e["paid_through_name"], ada)
        g = {x["key"]: x["docs"] for x in dok["groups"]}
        cek("T2 documents anchor.position_text == biaya_daftar.position_text", dok["anchor"]["position_text"] == harap,
            f"{dok['anchor']['position_text']!r} vs {harap!r}")
        cek("T2 documents jurnal = expenses.journal_id, total = total_amount",
            [x["id"] for x in g.get("jurnal", [])] == [str(e["journal_id"])]
            and Decimal(dok["anchor"]["total"]) == Decimal(str(e["total_amount"])), str(g.get("jurnal")))

        r = await req(c, NY, f"/api/expenses/{eid}/history")
        h = S.ExpenseHistoryResponse.model_validate(await EX.get_expense_history(r, eid, 200)).model_dump(mode="json")["data"]
        jenis = [x["jenis"] for x in h["events"]]
        cek("T3 history dibuat + diposting, terbaru dulu", "BIAYA_DIBUAT" in jenis and "BIAYA_DIPOSTING" in jenis
            and jenis.index("BIAYA_DIPOSTING") < jenis.index("BIAYA_DIBUAT"), str(jenis))

        # T4: draf salinan dari baris terbit (HANYA di salinan)
        did = uuid.uuid4()
        await c.execute("""INSERT INTO expenses (id, tenant_id, expense_number, expense_date, paid_through_id, paid_through_name,
                               paid_through_coa_id, vendor_id, vendor_name, account_id, account_name, currency, subtotal, tax_id, tax_name,
                               tax_rate, tax_amount, pph_type, pph_rate, pph_amount, total_amount, is_itemized, status, notes, created_by)
                           SELECT $1, tenant_id, 'EXP-UJI-T2', CURRENT_DATE, paid_through_id, paid_through_name, paid_through_coa_id, vendor_id,
                               vendor_name, account_id, account_name, currency, subtotal, tax_id, tax_name, tax_rate, tax_amount, pph_type,
                               pph_rate, pph_amount, total_amount, false, 'draft', 'uji salinan t2', created_by
                           FROM expenses WHERE id = $2 AND tenant_id = $3""", did, eid, NY)
        sebelum = await hitung(c, NY)
        r = await req(c, NY, f"/api/expenses/{did}/post/preview", "POST")
        p = (await EX.preview_post_expense(r, did))["data"]
        j = (p["journals"] or [{}])[0]
        db = sum(Decimal(str(x["debit"])) for x in j.get("lines", []))
        kr = sum(Decimal(str(x["credit"])) for x in j.get("lines", []))
        cek("T4 pratinjau terbitkan: can_proceed, jurnal EXPENSE seimbang",
            p["can_proceed"] and j.get("source_type") == "EXPENSE" and db == kr > 0, str(p)[:300])
        st = await c.fetchval("SELECT status FROM expenses WHERE id = $1 AND tenant_id = $2", did, NY)
        cek("T4 pratinjau NOL tulis (jurnal, bank, status draf)", await hitung(c, NY) == sebelum and st == "draft",
            f"{sebelum} -> {await hitung(c, NY)}, status={st}")
        r = await req(c, NY, f"/api/expenses/{did}")
        a = S.ExpenseDetailResponse.model_validate(await EX.get_expense_detail(r, did)).model_dump(mode="json")["aksi_tersedia"]
        cek("T4 aksi draf: posting tersedia == pratinjau can_proceed", a["posting"]["tersedia"] is p["can_proceed"], str(a["posting"]))

        # T5 pratinjau batalkan
        sebelum = await hitung(c, NY)
        r = await req(c, NY, f"/api/expenses/{eid}/void/preview", "POST")
        p = (await EX.preview_void_expense(r, eid, S.VoidExpenseRequest(reason="uji pratinjau")))["data"]
        j = (p["journals"] or [{}])[0]
        cek("T5 pratinjau batalkan: jurnal EXPENSE_REVERSAL ber-alasan", p["can_proceed"] and j.get("source_type") == "EXPENSE_REVERSAL"
            and "uji pratinjau" in (j.get("description") or ""), str(p)[:300])
        st = await c.fetchval("SELECT status FROM expenses WHERE id = $1 AND tenant_id = $2", eid, NY)
        cek("T5 pratinjau NOL tulis", await hitung(c, NY) == sebelum and st == "posted", f"{sebelum} -> {await hitung(c, NY)}, {st}")
        r = await req(c, NY, f"/api/expenses/{eid}")
        a = S.ExpenseDetailResponse.model_validate(await EX.get_expense_detail(r, eid)).model_dump(mode="json")["aksi_tersedia"]
        cek("T5 aksi batalkan == pratinjau can_proceed", a["batalkan"]["tersedia"] is p["can_proceed"], str(a["batalkan"]))

        # T6 batalkan NYATA (salinan)
        r = await req(c, NY, f"/api/expenses/{eid}/void", "POST")
        await EX.void_expense(r, eid, S.VoidExpenseRequest(reason="salah input uji"))
        r = await req(c, NY, f"/api/expenses/{eid}/history")
        h = (await EX.get_expense_history(r, eid, 200))["data"]
        bt = [x for x in h["events"] if x["jenis"] == "BIAYA_DIBATALKAN"]
        cek("T6 history BIAYA_DIBATALKAN dengan alasan", len(bt) == 1 and bt[0]["ringkas"].endswith(": salah input uji"), str(bt))
        r = await req(c, NY, f"/api/expenses/{eid}/documents")
        dok = S.ExpenseDocumentsResponse.model_validate(await EX.get_expense_documents(r, eid)).model_dump(mode="json")["data"]
        g = {x["key"]: x["docs"] for x in dok["groups"]}
        cek("T6 documents: Dibatalkan + jurnal asli & pembalik", dok["anchor"]["position_text"] == "Dibatalkan"
            and [x["status"] for x in g.get("jurnal", [])] == ["diposting", "pembalik"], str(g.get("jurnal")))
        r = await req(c, NY, f"/api/expenses/{eid}")
        a = (await EX.get_expense_detail(r, eid))["aksi_tersedia"]
        cek("T6 aksi batalkan tak tersedia: sudah dibatalkan", a["batalkan"] == {"tersedia": False, "alasan": "Biaya sudah dibatalkan."}, str(a["batalkan"]))

        # T7 404 + lintas tenant
        acak = uuid.uuid4()
        kodes = [await kode404(EX.get_expense_documents(await req(c, NY, "/x"), acak)),
                 await kode404(EX.get_expense_history(await req(c, NY, "/x"), acak, 200)),
                 await kode404(EX.get_expense_documents(await req(c, KA, "/x"), eid)),
                 await kode404(EX.get_expense_history(await req(c, KA, "/x"), eid, 200))]
        cek("T7 404 id acak + lintas tenant (id nyawit, tenant kaos)", kodes == [404] * 4, str(kodes))

        # kaos: documents/history sekadar jalan + model valid atas data nyata (termasuk biaya void)
        ok = True
        for row in await c.fetch("SELECT id FROM expenses WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 10", KA):
            S.ExpenseDocumentsResponse.model_validate(await EX.get_expense_documents(await req(c, KA, "/x"), row["id"]))
            hh = S.ExpenseHistoryResponse.model_validate(await EX.get_expense_history(await req(c, KA, "/x"), row["id"], 200))
            ok = ok and any(x["jenis"] == "BIAYA_DIBUAT" for x in hh.data.events)
        cek("T8 kaos 10 biaya terbaru: documents/history valid, BIAYA_DIBUAT ada", ok)

    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
