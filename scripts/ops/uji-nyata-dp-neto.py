"""Harness NYATA DP-NETO (KONTRAK-dp-neto-20261016). argv: lama|baru <out.json>  ATAU  banding lama.json baru.json.
lama/baru = kode pohon utama / cabang dipasang di /app/backend. BACA grapgrap + kaos dalam transaksi read-only
(ROLLBACK), lalu LENGAN TULIS kaos 'TES E2E' dalam transaksi terpisah yang SELALU di-ROLLBACK:
SO-2609-0331 (DP 50.000, refund 10.000, total 400.000) -> uang muka sebesar sisa NETO 360.000 lewat rute create.
Dibuang per SO ber-DP: ringkasan_pesanan, received_total_for_order (plafon), deposit_totals_for_order, muat_atribusi,
terbayar_proforma, batas_bawah_total, uang_muka_so; per uang muka ber-SO: order_remaining PDF kwitansi."""
import asyncio, json, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from datetime import date
from types import SimpleNamespace

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1

def _kunci(o):
    if isinstance(o, dict):
        return {str(k): _kunci(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_kunci(v) for v in o]
    return o


def js(o):
    return json.loads(json.dumps(_kunci(o), sort_keys=True, default=str))

if sys.argv[1] == "banding":
    a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
    cek(sorted(a) == sorted(b), "kunci sama", f"{len(a)} entri")
    from decimal import Decimal, InvalidOperation
    def angka(o):  # bandingkan NILAI, bukan representasi ("50000.0" float lama vs "50000.00" Decimal jurnal)
        if isinstance(o, dict):
            return {k: angka(v) for k, v in o.items()}
        if isinstance(o, list):
            return [angka(v) for v in o]
        if isinstance(o, (int, float)) and not isinstance(o, bool):
            return Decimal(str(o)).normalize()
        if isinstance(o, str):
            try:
                return Decimal(o).normalize()
            except InvalidOperation:
                return o
        return o
    beda = sorted(k for k in a if angka(a[k]) != angka(b.get(k)))
    g = [k for k in beda if k.startswith("grapgrap")]
    cek(not g, "grapgrap lama == baru (semua SO ber-DP, semua fungsi)", str(g[:5]))
    so_k = sorted({k.split("|")[1] for k in beda if k.startswith("kaos") and not k.split("|")[1].startswith("dep:")})
    dep_k = sorted({k.split("|")[1][4:] for k in beda if k.startswith("kaos") and k.split("|")[1].startswith("dep:")})
    print("  kaos SO berubah:", so_k)
    for k in beda:
        if k.startswith("kaos") and "|ringkasan" not in k:
            print("   ", k, "\n      lama:", json.dumps(a[k])[:260], "\n      baru:", json.dumps(b.get(k))[:260])
    cek(not [k for k in beda if "|ringkasan" in k], "ringkasan_pesanan (Sisa SO) identik di kedua tenant")
    cek(set(so_k) <= {"SO-2609-0331", "SO-2609-0003"}, "kaos: hanya SO ber-refund/ber-draf yang berubah", str(so_k))
    so_dep = {d: b.get(f"kaos-biru-konveksi|dep:{d}|so") for d in dep_k}
    cek(set(so_dep.values()) <= {"SO-2609-0331", "SO-2609-0003"}, f"kaos: kwitansi berubah ({len(dep_k)}) hanya milik SO itu", str(so_dep))
    print("RINGKAS gagal=%d" % gagal); sys.exit(0)

import asyncpg
from fastapi import HTTPException
MODE, OUT = sys.argv[1], sys.argv[2]
from backend.api_gateway.app.routers import customer_deposits as CD, proformas as PF
from backend.api_gateway.app.services import proforma_terbayar as PT, proforma_atribusi as PA
from backend.api_gateway.app.services.so_ubah_terkonfirmasi import batas_bawah_total


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    CD.get_pool = gp
    dump = {}
    tx = c.transaction(readonly=True); await tx.start()
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            so = await c.fetch("""SELECT DISTINCT so.id, so.order_number, so.total_amount FROM customer_deposits cd
                LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
                JOIN sales_orders so ON so.id = COALESCE(cd.sales_order_id, p.sales_order_id) AND so.tenant_id = cd.tenant_id
                WHERE cd.tenant_id = $1""", t)
            ids = [r["id"] for r in so]
            rg = await PT.ringkasan_pesanan(c, t, ids)
            at = await PA.muat_atribusi(c, t, ids)
            tb = await PT.terbayar_proforma(c, t, ids)
            for r in so:
                s, n = r["id"], r["order_number"]
                dump[f"{t}|{n}|ringkasan"] = js(rg[s])
                dump[f"{t}|{n}|plafon_received"] = await CD.received_total_for_order(c, t, s)
                dump[f"{t}|{n}|panel_proforma"] = js(await PF.deposit_totals_for_order(c, t, s))
                dump[f"{t}|{n}|atribusi"] = js({k: v for k, v in at[s].items() if k != "nomor"})
                dump[f"{t}|{n}|terbayar_proforma"] = js({str(p): v["paid"] for p, v in tb.items()
                                                       if p in {x["id"] for x in await c.fetch("SELECT id FROM proformas WHERE sales_order_id=$1", s)}})
                dump[f"{t}|{n}|batas_ubah"] = js(await batas_bawah_total(c, t, s, r["total_amount"]))
                dump[f"{t}|{n}|uang_muka_so"] = js(await PA.uang_muka_so(c, t, s))
                if MODE == "baru":
                    from backend.api_gateway.app.services.uang_muka_pesanan import uang_muka_pesanan
                    u = (await uang_muka_pesanan(c, t, [s]))[s]
                    if u["neto"] != rg[s]["dp_received"] - rg[s]["dp_refunded"]:
                        cek(False, f"{t} {n} neto == ringkasan diterima − refund")
            for d in await c.fetch("SELECT id FROM customer_deposits WHERE tenant_id=$1 AND sales_order_id IS NOT NULL AND status <> 'void'", t):
                k = await CD.muat_pdf_kwitansi_uang_muka(c, {"tenant_id": t, "user_id": None}, str(d["id"]))
                dump[f"{t}|dep:{d['id']}|kwitansi_sisa"] = k["receipt_data"].get("order_remaining")
                dump[f"{t}|dep:{d['id']}|so"] = await c.fetchval(
                    "SELECT so.order_number FROM customer_deposits cd JOIN sales_orders so ON so.id = cd.sales_order_id WHERE cd.id = $1", d["id"])
            if MODE == "baru":
                cek(True, f"{t} neto == ringkasan diterima − refund ({len(ids)} SO; FAIL tercetak bila ada)")
    finally:
        await tx.rollback()
    # ---- LENGAN TULIS kaos (ROLLBACK selalu)
    t = "kaos-biru-konveksi"
    so = await c.fetchrow("SELECT id, customer_id, customer_name, total_amount FROM sales_orders WHERE tenant_id=$1 AND order_number='SO-2609-0331'", t)
    dep0 = await c.fetchrow("SELECT account_id, payment_method, bank_account_id FROM customer_deposits WHERE sales_order_id=$1 AND status<>'void' LIMIT 1", so["id"])
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={}, client=None)
    hasil = {}
    for jml in (360000, 360001):
        tx = c.transaction(); await tx.start()
        try:
            body = CD.CreateCustomerDepositRequest(customer_id=str(so["customer_id"]), customer_name=so["customer_name"],
                                                  amount=jml, deposit_date=date(2026, 10, 16), account_id=str(dep0["account_id"]),
                                                  payment_method=dep0["payment_method"],
                                                  bank_account_id=str(dep0["bank_account_id"]) if dep0["bank_account_id"] else None,
                                                  sales_order_id=str(so["id"]), notes="TES E2E dp-neto (ROLLBACK)")
            try:
                r = await CD.create_customer_deposit(req, body)
                hasil[jml] = "DITERIMA"
            except HTTPException as e:
                hasil[jml] = f"TOLAK {e.status_code}: {str(e.detail)[:120]}"
        finally:
            await tx.rollback()
    print("  lengan tulis kaos SO-2609-0331:", hasil)
    if MODE == "baru":
        cek(hasil[360000] == "DITERIMA", "TULIS kaos: pembayaran pesanan sebesar sisa NETO 360.000 DITERIMA sesudah refund sebagian")
        cek(hasil[360001].startswith("TOLAK 400"), "TULIS kaos: 1 rupiah di atas sisa neto tetap DITOLAK")
        k = [v for kk, v in dump.items() if kk.startswith(t) and "kwitansi_sisa" in kk and v is not None]
        r0331 = dump[f"{t}|SO-2609-0331|batas_ubah"]
        cek(float(r0331["uang_muka_diterima"]) == 40000.0, "batas ubah SO-2609-0331: uang muka diterima neto 40.000", str(r0331["uang_muka_diterima"]))
        cek(float(r0331["batas"]) == 40000.0 and float(r0331["tertagih_proforma"]) == 40000.0,
            "batas ubah SO-2609-0331 SELURUHNYA 40.000 (termasuk tertagih proforma via atribusi neto)",
            f'{r0331["batas"]} / {r0331["tertagih_proforma"]}')
        cek(dump[f"{t}|SO-2609-0331|plafon_received"] == 40000.0, "plafon SO-2609-0331 = neto 40.000")
        kw = [v for kk, v in dump.items() if kk.startswith(f"{t}|dep:") and kk.endswith("|kwitansi_sisa")
              and dump.get(kk.replace("|kwitansi_sisa", "|so")) == "SO-2609-0331"]
        cek(kw == [360000], "kwitansi SO-2609-0331: Sisa Nilai Pesanan 360.000 (neto refund)", str(kw))
    else:
        print("  (lama) lengan tulis = bukti perilaku lama")
    await c.close()
    json.dump(dump, open(OUT, "w"), sort_keys=True, default=str)
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
