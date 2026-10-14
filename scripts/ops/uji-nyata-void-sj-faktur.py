"""Harness NYATA VOID-SJ-FAKTUR (15 Okt 2026). kaos: TULIS di dalam transaksi yang SELALU di-ROLLBACK (savepoint per skenario);
grapgrap: BACA saja (pratinjau di BEGIN READ ONLY), NOL tulisan.
Skenario kaos:
  A  SJ-2609-0003 (ber-stok, INV-2609-0048 posted): void mandiri -> 2 jurnal pembalik seimbang + cermin, kartu +qty (journal_id pembalik),
     cache baris faktur turun, faktur kembali perlu dikirim, AR/status faktur tetap, Check 15/16 tak memburuk.
  B  SJ-2610-0006 (non-stok, INV-2610-0009): nol jurnal, nol kartu; faktur kembali perlu dikirim; AR tetap.
  C  INV-2608-0001 (2 SJ: SJ-2607-0001/0002): void SJ-2607-0002 mandiri lalu void FAKTUR (tulisan void faktur) -> tiap baris kartu
     INVOICE_FULFILLMENT dibalik TEPAT sekali; Σ keluar == Σ pembalik; jurnal HPP/pendapatan tiap SJ dibalik tepat sekali.
  D  idempoten: void kedua atas SJ yang sama = ulang tanpa tulisan.
MODE=merah: hanya menjalankan C dengan pemeriksaan yang sama (dipakai kontrol merah: kode disabot harus GAGAL)."""
import asyncio, os, sys
sys.path.insert(0, "/app")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness-tanpa-jaringan")
import asyncpg
from decimal import Decimal
from backend.api_gateway.app.services import sj_so as S
from backend.api_gateway.app.routers import sales_invoices as SI

K, G = "kaos-biru-konveksi", "grapgrap-manado"
gagal = 0


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1


async def sj(c, t, nomor):
    return await c.fetchrow("SELECT * FROM invoice_fulfillments WHERE tenant_id=$1 AND fulfillment_number=$2", t, nomor)


async def potret(c, t, inv_id):
    si = await c.fetchrow("SELECT status, fulfillment_status, revenue_status, total_fulfilled_qty, total_recognized_amount "
                          "FROM sales_invoices WHERE id=$1", inv_id)
    ar = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", t, inv_id)
    items = {r["id"]: (r["fulfilled_qty"], r["recognized_amount"]) for r in await c.fetch(
        "SELECT id, fulfilled_qty, recognized_amount FROM sales_invoice_items WHERE invoice_id=$1", inv_id)}
    return {"si": dict(si), "ar": ar, "items": items}


async def cek_mutu(c, t, label):
    d = {r["tenant_id"]: r for r in await c.fetch("SELECT * FROM verify_deferred_revenue_reconciliation_all()")}.get(t)
    w = {r["tenant_id"]: r for r in await c.fetch("SELECT * FROM verify_inventory_wac_reconciliation_all()")}.get(t)
    return {"c16": (d["verdict"], d["drift"]) if d else None, "c15": (w["verdict"], w["drift"]) if w else None}


async def jurnal_seimbang_cermin(c, rid):
    h = await c.fetchrow("SELECT total_debit, total_credit, reversal_of_id, status FROM journal_entries WHERE id=$1", rid)
    bl = await c.fetch("SELECT account_id, debit, credit FROM journal_lines WHERE journal_id=$1 ORDER BY line_number", rid)
    ol = await c.fetch("SELECT account_id, debit, credit FROM journal_lines WHERE journal_id=$1 ORDER BY line_number", h["reversal_of_id"])
    seimbang = sum(x["debit"] for x in bl) == sum(x["credit"] for x in bl) == h["total_debit"] == h["total_credit"]
    cermin = [(x["account_id"], x["debit"], x["credit"]) for x in bl] == [(x["account_id"], x["credit"], x["debit"]) for x in ol]
    return seimbang and cermin and h["status"] == "POSTED"


async def skenario_c(c, ctx):
    inv_id = await c.fetchval("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND invoice_number='INV-2608-0001'", K)
    s1, s2 = await sj(c, K, "SJ-2607-0001"), await sj(c, K, "SJ-2607-0002")
    asli = await c.fetch("SELECT id, quantity_out, journal_id FROM inventory_ledger WHERE tenant_id=$1 AND source_type='INVOICE_FULFILLMENT' "
                         "AND source_id=$2", K, inv_id)
    stok0 = await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out),0) FROM inventory_ledger WHERE tenant_id=$1 AND source_id=$2", K, inv_id)
    await S.void(c, ctx, s2["id"], "UJI HARNESS void SJ mandiri")
    r = await SI._rencana_void_faktur(c, ctx, inv_id)
    print("JSON C blok void faktur (diabaikan di harness, hanya tulisan stok yang diuji):", [b["code"] for b in r["blocks"]])
    cek(len(r["fulfillments"]) == 1 and r["fulfillments"][0]["id"] == s1["id"], "C void faktur hanya memproses SJ posted (SJ-2607-0001)")

    class Body:
        reason = "UJI HARNESS void faktur"
    await SI._tulis_void_faktur(c, ctx, inv_id, r, Body())
    balik = await c.fetch("SELECT quantity_in, journal_id FROM inventory_ledger WHERE tenant_id=$1 AND source_type='INVOICE_FULFILLMENT_VOID' "
                          "AND source_id=$2", K, inv_id)
    cek(len(balik) == len(asli), "C jumlah baris pembalik kartu == baris keluar asli (tanpa dobel)", f"{len(balik)} vs {len(asli)}")
    cek(sum(x["quantity_in"] for x in balik) == sum(x["quantity_out"] for x in asli), "C Σ pembalik qty == Σ keluar qty")
    stok1 = await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out),0) FROM inventory_ledger WHERE tenant_id=$1 AND source_id=$2", K, inv_id)
    cek(stok1 == stok0 + sum(x["quantity_out"] for x in asli), "C saldo kartu faktur kembali tepat (netto 0 dari SJ)", f"{stok0} -> {stok1}")
    for f in (s1, s2):
        for jid in (f["journal_id"], f["revenue_journal_id"]):
            if jid:
                n = await c.fetchval("SELECT count(*) FROM journal_entries WHERE reversal_of_id=$1", jid)
                cek(n == 1, f"C jurnal {f['fulfillment_number']} dibalik tepat sekali", str(n))


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', K)
    ctx = {"tenant_id": K, "user_id": uid}
    if os.environ.get("MODE") == "merah":
        tx = c.transaction(); await tx.start()
        try:
            await skenario_c(c, ctx)
        finally:
            await tx.rollback()
        print("RINGKAS gagal=%d" % gagal); return
    # grapgrap: BACA saja
    await c.execute("BEGIN READ ONLY")
    try:
        g = await c.fetchrow("SELECT id FROM invoice_fulfillments WHERE tenant_id=$1 AND fulfillment_number='SJ-2610-0001'", G)
        rg = await S.rencana_void(c, G, g["id"], "pratinjau")
        cek(rg["sj"]["asal"] == "faktur" and rg["blocks"] == [], "grapgrap pratinjau SJ-2610-0001 (non-stok) lolos tanpa flag, baca saja",
            str([b["code"] for b in rg["blocks"]]))
    finally:
        await c.execute("ROLLBACK")
    tx = c.transaction(); await tx.start()
    try:
        mutu0 = await cek_mutu(c, K, "awal")
        # A ber-stok
        a = await sj(c, K, "SJ-2609-0003")
        pa0 = await potret(c, K, a["invoice_id"])
        ws0 = await c.fetchval("SELECT COALESCE(SUM(quantity),0) FROM warehouse_stock WHERE tenant_id=$1 AND warehouse_id=$2", K, a["warehouse_id"])
        sp = c.transaction(); await sp.start()
        pv = await S.rencana_void(c, K, a["id"], "uji")
        cek(pv["blocks"] == [] and any("tetap berlaku" in n for n in pv["notes"]), "A pratinjau lolos + catatan faktur tetap", str(pv["notes"]))
        await S.void(c, ctx, a["id"], "UJI HARNESS A")
        pa1 = await potret(c, K, a["invoice_id"])
        revs = await c.fetch("SELECT id, source_type FROM journal_entries WHERE reversal_of_id = ANY($1::uuid[])",
                             [a["journal_id"], a["revenue_journal_id"]])
        cek(sorted(r["source_type"] for r in revs) == ["INVOICE_FULFILLMENT_REVERSAL", "INVOICE_REVENUE_REVERSAL"], "A dua jurnal pembalik")
        for r in revs:
            cek(await jurnal_seimbang_cermin(c, r["id"]), f"A {r['source_type']} seimbang (Σbaris==header) + cermin + POSTED")
        kartu = await c.fetch("SELECT quantity_in, journal_id FROM inventory_ledger WHERE tenant_id=$1 AND source_type='INVOICE_FULFILLMENT_VOID' "
                              "AND source_id=$2", K, a["invoice_id"])
        q_sj = await c.fetchval("SELECT SUM(quantity) FROM invoice_fulfillment_items WHERE fulfillment_id=$1", a["id"])
        hpp_rev = next(r["id"] for r in revs if r["source_type"] == "INVOICE_FULFILLMENT_REVERSAL")
        cek(len(kartu) == 1 and kartu[0]["quantity_in"] == q_sj and kartu[0]["journal_id"] == hpp_rev, "A kartu +qty SJ, journal_id = pembalik HPP")
        ws1 = await c.fetchval("SELECT COALESCE(SUM(quantity),0) FROM warehouse_stock WHERE tenant_id=$1 AND warehouse_id=$2", K, a["warehouse_id"])
        cek(ws1 - ws0 == q_sj, "A stok gudang naik qty SJ (trigger)", f"{ws0} -> {ws1}")
        cek(pa1["ar"] == pa0["ar"] and pa1["si"]["status"] == pa0["si"]["status"], "A piutang + status faktur TETAP",
            f"{pa0['ar']} {pa0['si']['status']}")
        cek(pa0["si"]["fulfillment_status"] == "fulfilled" and pa1["si"]["fulfillment_status"] in SI._so_kirim.KIRIM_TERBUKA,
            "A faktur kembali perlu dikirim", f"{pa0['si']['fulfillment_status']} -> {pa1['si']['fulfillment_status']}")
        cek(pa1["si"]["total_recognized_amount"] == pa0["si"]["total_recognized_amount"] - pv["total_pendapatan"],
            "A recognized turun == jurnal pendapatan", str(pv["total_pendapatan"]))
        st = await c.fetchval("SELECT status FROM invoice_fulfillments WHERE id=$1", a["id"])
        cek(st == "voided", "A SJ voided")
        mutu1 = await cek_mutu(c, K, "A")
        cek(mutu1 == mutu0, "A Check 15/16 kaos tak berubah", f"{mutu0} -> {mutu1}")
        h2 = await S.void(c, ctx, a["id"], "UJI HARNESS ulang")
        n_rev = await c.fetchval("SELECT count(*) FROM journal_entries WHERE reversal_of_id = ANY($1::uuid[])", [a["journal_id"], a["revenue_journal_id"]])
        cek(h2 == {"ulang": True} and n_rev == 2, "D void kedua = ulang tanpa tulisan")
        await sp.rollback()
        # B non-stok
        b = await sj(c, K, "SJ-2610-0006")
        sp = c.transaction(); await sp.start()
        pb0 = await potret(c, K, b["invoice_id"])
        n_je0 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", K)
        n_il0 = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1", K)
        await S.void(c, ctx, b["id"], "UJI HARNESS B")
        pb1 = await potret(c, K, b["invoice_id"])
        cek(await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", K) == n_je0 and
            await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1", K) == n_il0, "B non-stok: nol jurnal, nol kartu")
        cek(pb1["ar"] == pb0["ar"] and pb1["si"]["fulfillment_status"] in SI._so_kirim.KIRIM_TERBUKA, "B AR tetap, faktur kembali perlu dikirim",
            f"{pb0['si']['fulfillment_status']} -> {pb1['si']['fulfillment_status']}")
        await sp.rollback()
        # C dua SJ + void faktur
        sp = c.transaction(); await sp.start()
        await skenario_c(c, ctx)
        await sp.rollback()
    finally:
        await tx.rollback()
        await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
