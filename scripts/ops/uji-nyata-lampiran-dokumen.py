"""Harness NYATA LAMPIRAN-TANPA-SO (15 Okt 2026). grapgrap + kaos BACA (BEGIN READ ONLY); satu kontrol kaos TULIS di tx yang
SELALU di-ROLLBACK (lampiran uji ditautkan ke faktur tanpa SO + penerimaannya).
  P1 benih kosong == SQL keluarga LAMA (dibaca dari /lama.py = berkas master) untuk SETIAP SO kedua tenant (baris identik).
  P2 untuk SETIAP dokumen di keluarga SO mana pun: tentukan() -> tepat satu SO => hasil generik == /sales-orders/{so}/lampiran (susun);
     lebih dari satu SO => mode dokumen (dihitung, dicetak).
  P3 setiap dokumen berlampiran (hub + faktur lama) terjangkau generik dengan berkasnya sendiri ikut tampil.
  K1 id bill_payments_v2 lewat jenis receive_payment -> 404.
  K2 kaos INV-2609-0048 (tanpa SO) + RCV-2026-0008: lampiran uji di tx-ROLLBACK -> mode dokumen, grup sales_invoice + receive_payment;
     generik lewat penerimaan memberi grup yang sama.
MODE=merah: hanya P1 + K2 (kontrol merah: sabotase harus GAGAL)."""
import ast, asyncio, os, sys
sys.path.insert(0, "/app")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka")
import asyncpg
from fastapi import HTTPException
from backend.api_gateway.app.routers import sales_order_lampiran as SL, lampiran_dokumen as LD

TENANT = ("grapgrap-manado", "kaos-biru-konveksi")
gagal = 0


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1


def sql_lama():
    t = ast.parse(open("/lama.py").read())
    for n in t.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_SQL_KELUARGA":
            return n.value.value


def kunci(rows):
    return sorted((r["jenis"], str(r["dok_id"]), str(r["lampiran_id"]), r["sumber"]) for r in rows)


def susun(rows):
    return SL.susun_grup_lampiran(rows, {j: True for j in SL.URUTAN_JENIS})


async def p1_p2_p3(c):
    lama = sql_lama()
    for t in TENANT:
        sos = [r["id"] for r in await c.fetch("SELECT id FROM sales_orders WHERE tenant_id=$1", t)]
        beda1 = 0
        anggota = {}
        for s in sos:
            baru = await SL.baris_keluarga(c, t, s)
            if kunci(baru) != kunci(await c.fetch(lama, s, t)):
                beda1 += 1
            for m in await c.fetch(SL._SQL_META_KELUARGA + ") SELECT jenis, id FROM meta", *SL.param_keluarga(s, t)):
                if m["jenis"] in LD.JENIS_DOKUMEN:
                    anggota.setdefault((m["jenis"], m["id"]), set()).add(s)
        cek(beda1 == 0, f"P1 {t} benih kosong == SQL lama di {len(sos)} SO", f"beda {beda1}")
        if os.environ.get("MODE") == "merah":
            continue
        beda2 = banyak = 0
        for (j, d), keluarga in anggota.items():
            r = await LD.tentukan(c, t, j, d)
            if set(r["so"]) != keluarga:
                beda2 += 1
                print("JSON", t, "P2 so beda", j, d, sorted(map(str, r["so"])), sorted(map(str, keluarga)))
                continue
            if len(r["so"]) == 1:
                if susun(await SL.baris_keluarga(c, t, r["so"][0])) != susun(await SL.baris_keluarga(c, t, list(keluarga)[0])):
                    beda2 += 1
            else:
                banyak += 1
        cek(beda2 == 0, f"P2 {t} {len(anggota)} dokumen keluarga: SO terpilih == keluarga yang memuatnya; generik == keluarga SO",
            f"beda {beda2}, >1 SO (mode dokumen) {banyak}")
        # P3 dokumen berlampiran
        dok = await c.fetch(
            """SELECT CASE da.entity_type WHEN 'payment' THEN 'receive_payment' ELSE da.entity_type END AS jenis, da.entity_id AS id,
                      da.document_id AS lid FROM document_attachments da JOIN documents d ON d.id = da.document_id AND d.deleted_at IS NULL
               WHERE da.tenant_id = $1 AND da.entity_type IN ('sales_order','sales_invoice','payment','customer_deposit','proforma',
                     'delivery','credit_note')
                 AND (da.entity_type <> 'payment' OR EXISTS (SELECT 1 FROM receive_payments r WHERE r.id = da.entity_id))
               UNION ALL
               SELECT 'sales_invoice', sa.invoice_id, sa.id FROM sales_invoice_attachments sa
               WHERE sa.tenant_id = $1 AND EXISTS (SELECT 1 FROM sales_invoices s WHERE s.id = sa.invoice_id)""", t)
        hilang = 0
        for x in dok:
            r = await LD.tentukan(c, t, x["jenis"], x["id"])
            rows = (await SL.baris_keluarga(c, t, r["so"][0]) if len(r["so"]) == 1
                    else await SL.baris_keluarga(c, t, None, {x["jenis"]: [x["id"]]}))
            if not any(str(b["dok_id"]) == str(x["id"]) and str(b["lampiran_id"]) == str(x["lid"]) for b in rows):
                hilang += 1
                print("JSON", t, "P3 hilang", x["jenis"], x["id"])
        cek(hilang == 0, f"P3 {t} {len(dok)} berkas dokumen penjualan muncul di generik dokumennya", f"hilang {hilang}")


async def k1(c):
    bp = await c.fetchrow("""SELECT da.tenant_id, da.entity_id FROM document_attachments da
        WHERE da.entity_type = 'payment' AND da.tenant_id = 'grapgrap-manado'
          AND EXISTS (SELECT 1 FROM bill_payments_v2 b WHERE b.id = da.entity_id) LIMIT 1""")
    try:
        await LD.tentukan(c, bp["tenant_id"], "receive_payment", bp["entity_id"])
        cek(False, "K1 id bill_payments_v2 lewat receive_payment -> 404")
    except HTTPException as e:
        cek(e.status_code == 404, "K1 id bill_payments_v2 lewat receive_payment -> 404", str(e.status_code))


async def k2(c):
    t = "kaos-biru-konveksi"
    inv = await c.fetchrow("SELECT id, sales_order_id FROM sales_invoices WHERE tenant_id=$1 AND invoice_number='INV-2609-0048'", t)
    rp = await c.fetchval("SELECT id FROM receive_payments WHERE tenant_id=$1 AND payment_number='RCV-2026-0008'", t)
    doc = await c.fetchval("SELECT id FROM documents WHERE tenant_id=$1 AND deleted_at IS NULL AND storage_type='s3' LIMIT 1", t)
    cek(inv["sales_order_id"] is None, "K2 prasyarat: INV-2609-0048 tanpa SO")
    tx = c.transaction(); await tx.start()
    try:
        for et, eid in (("sales_invoice", inv["id"]), ("payment", rp)):
            await c.execute("""INSERT INTO document_attachments (id, tenant_id, document_id, entity_type, entity_id, display_name)
                               VALUES (gen_random_uuid(), $1, $2, $3, $4, 'UJI HARNESS lampiran.pdf')""", t, doc, et, eid)
        for jenis, did in (("sales_invoice", inv["id"]), ("receive_payment", rp)):
            r = await LD.tentukan(c, t, jenis, did)
            rows = await SL.baris_keluarga(c, t, None, {jenis: [did]}) if len(r["so"]) != 1 else []
            g = {x["jenis"] for x in rows}
            cek(r["so"] == [] and {"sales_invoice", "receive_payment"} <= g,
                f"K2 {jenis} tanpa SO -> mode dokumen, grup faktur + penerimaan", str(sorted(g)))
    finally:
        await tx.rollback()


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await c.execute("BEGIN READ ONLY")
        try:
            await p1_p2_p3(c)
            if os.environ.get("MODE") != "merah":
                await k1(c)
        finally:
            await c.execute("ROLLBACK")
        tx = c.transaction(); await tx.start()
        try:
            await k2(c)
        finally:
            await tx.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM document_attachments WHERE display_name = 'UJI HARNESS lampiran.pdf'")
        cek(sisa == 0, "jejak uji 0 sesudah ROLLBACK", str(sisa))
    finally:
        await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
