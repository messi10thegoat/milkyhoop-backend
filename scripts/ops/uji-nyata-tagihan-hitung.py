"""UJI NYATA penentu hitung + blok Faktur Pembelian (22 Okt 2026, /bills/calculate + /bills/{id}/edit/preview). SATU transaksi SELALU ROLLBACK.
  A (grapgrap + kaos, baca): SETIAP tagihan ber-baris dihitung ulang dari kolom tersimpan lewat tagihan_rencana.hitung -> bandingkan grand_total,
    tax_amount, dpp kepala + tax_amount/dpp per baris dengan yang TERSIMPAN (ditulis kode create/update v2 LAMA). Beda = laporan (asal
    tagihan bukan v2, mis. dari PO/v1) -- dicetak, bukan gagal; yang dinilai: tagihan ber-pajak-per-baris/ber-diskon kepala.
  B (kaos, sintetis 'TES E2E' di transaksi ROLLBACK): tiap skenario -> POST /bills/calculate (data) == baris tersimpan create_bill_v2
    (kepala + per baris); edit/preview == tersimpan update_bill_v2; blok calculate == pesan galat create; /calculate LAMA (pajak kepala tak
    dinolkan) menyimpang pada skenario pajak per baris (bukti perbaikan).
MERAH=1 : hitung_json disabotase (PPN baris tak masuk grand_total) -> B WAJIB gagal."""
import asyncio, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

TEN = ["grapgrap-manado", "kaos-biru-konveksi"]
MERAH = os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"))


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": str(uid)}; return r


async def main():
    from app.routers import bills as BR
    from app.schemas import bills as S
    from app.services import bills_service as BS
    from app.services import tagihan_rencana as TR
    from app.services.tax_factor import resolve_dpp_factor
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    BR.get_pool = gp
    svc = BS.BillsService(P())
    if MERAH:
        _hj = TR.hitung_json
        def rusak(r, h):
            out = _hj(r, h); out["grand_total"] = float(D(h["header"]["grand_total"])); return out
        TR.hitung_json = rusak
    try:
        # ---------------- A: tagihan tersimpan dihitung ulang
        for t in TEN:
            n = beda = 0; contoh = []
            for b in await c.fetch("SELECT * FROM bills WHERE tenant_id = $1 AND EXISTS (SELECT 1 FROM bill_items i WHERE i.bill_id = bills.id) ORDER BY created_at", t):
                its = await c.fetch("SELECT * FROM bill_items WHERE bill_id = $1 ORDER BY line_number", b["id"])
                r = {"items": [{"qty": i["quantity"], "price": i["unit_price"], "discount_percent": i["discount_percent"] or 0,
                                "tax_code_id": i["tax_code_id"], "tax_rate": float(i["tax_rate"] or 0)} for i in its],
                     "invoice_discount_percent": b["invoice_discount_percent"] or 0, "invoice_discount_amount": b["invoice_discount_amount"] or 0,
                     "cash_discount_percent": b["cash_discount_percent"] or 0, "cash_discount_amount": b["cash_discount_amount"] or 0,
                     "tax_rate": b["tax_rate"] or 0, "tax_code_id": b["tax_code_id"], "dpp_manual": b["dpp_manual"]}
                h = await TR.hitung(c, t, r)
                n += 1
                ok = (D(h["akhir"]["grand_total"]) == D(b["grand_total"]) and D(h["akhir"]["tax_amount"]) == D(b["tax_amount"])
                      and all(D(x["ppn"]) == D(i["tax_amount"]) for x, i in zip(h["baris"], its)))
                if not ok:
                    beda += 1
                    contoh.append((b["invoice_number"], str(D(b["grand_total"])), str(D(h["akhir"]["grand_total"])), str(D(b["tax_amount"])),
                                   str(D(h["akhir"]["tax_amount"])), "PO" if b["purchase_order_id"] else ""))
            print(f"LAPOR A {t}: {n} tagihan ber-baris dihitung ulang, beda dari tersimpan = {beda} {contoh[:8]}", flush=True)

        # ---------------- B: sintetis kaos
        T = "kaos-biru-konveksi"
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
        v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", T)
        kode = {r["rate"]: r["id"] for r in await c.fetch(
            "SELECT DISTINCT ON (rate) id, rate FROM tax_codes WHERE tenant_id = $1 AND rate IN (11, 12) ORDER BY rate, created_at", T)}
        dasar = {"vendor_id": str(v["id"]), "due_date": "2026-11-22", "issue_date": "2026-10-22", "notes": "TES E2E hitung"}
        brg = lambda nm, q, p, **k: {"product_name": nm, "qty": q, "price": p, **k}  # noqa: E731
        skenario = {
            "S1 pajak kepala 11": {**dasar, "tax_rate": 11, "items": [brg("TES A", 2, 15000), brg("TES B", 1, 7333.33)]},
            "S2 pajak per baris + diskon faktur% + tunai": {**dasar, "tax_rate": 11, "invoice_discount_percent": 10, "cash_discount_amount": 5000,
                                                            "items": [brg("TES A", 3, 12500, tax_rate=11), brg("TES B", 1, 40000, tax_rate=11, discount_percent=5)]},
            "S3 campur baris berpajak & tidak": {**dasar, "items": [brg("TES A", 1, 100000, tax_rate=11), brg("TES B", 4, 2500)]},
            "S4 dpp_manual kepala": {**dasar, "tax_rate": 11, "dpp_manual": 50000, "items": [brg("TES A", 1, 60000)]},
        }
        if 12 in kode:
            skenario["S5 kode 12% (faktor 11/12) per baris"] = {**dasar, "items": [brg("TES A", 2, 50000, tax_rate=12, tax_code_id=str(kode[12]))]}
        for nama, badan in skenario.items():
            body = S.CalculateBillRequestV2(**badan)
            hasil = await BR.calculate_bill_totals(req(T, uid), body)
            d = hasil["data"]
            cr = await svc.create_bill_v2(T, S.CreateBillRequestV2(**badan).model_dump(), uid)
            bid = uuid.UUID(cr["data"]["id"]) if cr.get("success") else None
            b = await c.fetchrow("SELECT * FROM bills WHERE id = $1", bid) if bid else None
            its = await c.fetch("SELECT * FROM bill_items WHERE bill_id = $1 ORDER BY line_number", bid) if bid else []
            beda_k = [(k, d[k], b[k]) for k in ("subtotal", "item_discount_total", "invoice_discount_total", "cash_discount_total", "dpp",
                                                "tax_amount", "grand_total") if b and (D(d[k]) != D(b[k]) if k != "dpp"
                      # bills.dpp = BIGINT (kolom tersimpan membulatkan ke rupiah; tiket) -> banding pada presisi kolom
                      else D(d[k]).quantize(Decimal("1")) != D(b[k]))]
            if b and beda_k:
                print(f"LAPOR {nama}: beda kepala {beda_k}", flush=True)
            ok = bool(b) and d["ok"] and not beda_k
            ok = ok and len(its) == len(d["items"]) and all(D(x["ppn"]) == D(i["tax_amount"]) and D(x["dpp"]) == D(i["dpp"]) and D(x["total"]) == D(i["total"])
                                                           for x, i in zip(d["items"], its))
            ok = ok and hasil["calculation"]["grand_total"] == d["grand_total"] == d["total_amount"]
            inv = D(d["subtotal"]) - D(d["discount_amount"]) + D(d["tax_amount"]) + D(d["adjustment"]) == D(d["total_amount"])
            cek(inv and ((D(d["adjustment"]) != 0) == ("dpp_manual" in badan)), f"B {nama}: ringkasan subtotal - discount + tax + adjustment == total",
                f"{d['subtotal']} - {d['discount_amount']} + {d['tax_amount']} + {d['adjustment']} = {d['total_amount']}")
            cek(ok, f"B {nama}: /calculate == tersimpan create v2", f"calc {d['grand_total']}/{d['tax_amount']} simpan {b and b['grand_total']}/{b and b['tax_amount']}")
            if "per baris" in nama:
                f = await resolve_dpp_factor(c, T, badan.get("tax_code_id"), badan.get("tax_rate", 0), "input")
                lama = BS.BillCalculator.calculate(items=body.model_dump()["items"], invoice_discount_percent=body.invoice_discount_percent,
                                                   invoice_discount_amount=body.invoice_discount_amount, cash_discount_percent=body.cash_discount_percent,
                                                   cash_discount_amount=body.cash_discount_amount, tax_rate=body.tax_rate, dpp_manual=body.dpp_manual, dpp_factor=f)
                print(f"LAPOR {nama}: /calculate LAMA grand_total {lama['grand_total']} vs tersimpan {b['grand_total']} (lama menyimpang = {D(lama['grand_total']) != D(b['grand_total'])})", flush=True)
            # edit/preview == update_bill_v2 tersimpan
            if bid:
                ubah = {"items": [brg("TES C", 5, 9999.99, tax_rate=11), brg("TES D", 1, 1234)], "invoice_discount_amount": 1000}
                pv = (await BR.preview_edit_bill(req(T, uid), bid, S.UpdateBillRequestV2(**ubah)))["data"]
                up = await svc.update_bill_v2(T, bid, S.UpdateBillRequestV2(**ubah).model_dump(exclude_unset=True), uid)
                b2 = await c.fetchrow("SELECT grand_total, tax_amount FROM bills WHERE id = $1", bid)
                cek(pv["ok"] and up.get("success") and D(pv["grand_total"]) == D(b2["grand_total"]) and D(pv["tax_amount"]) == D(b2["tax_amount"]),
                    f"B {nama}: edit/preview == tersimpan update v2", f"pv {pv.get('grand_total')} simpan {b2['grand_total']}")
        # blok == pesan create
        asing = {**dasar, "vendor_id": str(uuid.uuid4()), "items": [brg("TES A", 1, 1000)]}
        d = (await BR.calculate_bill_totals(req(T, uid), S.CalculateBillRequestV2(**asing)))["data"]
        cr = await svc.create_bill_v2(T, S.CreateBillRequestV2(**asing).model_dump(), uid)
        cek(not d["ok"] and d["blocks"][0]["code"] == "VENDOR_TIDAK_ADA" and cr["message"] == d["blocks"][0]["message"],
            "B blok vendor asing: calculate == pesan create", f"{d['blocks']} / {cr.get('message')}")
        kosong = {k: x for k, x in dasar.items() if k != "due_date"}
        d = (await BR.calculate_bill_totals(req(T, uid), S.CalculateBillRequestV2(**kosong)))["data"]
        cek(not d["ok"] and [b["code"] for b in d["blocks"]] == ["ITEM_KOSONG", "JATUH_TEMPO_WAJIB"], "B blok item kosong + due_date kosong (200, bukan 422)", str(d["blocks"]))
        cr = await svc.create_bill_v2(T, {**kosong, "items": [brg("TES A", 1, 1000)]}, uid)
        cek(cr["message"] == "Tanggal jatuh tempo (due_date) wajib diisi", "B create tanpa due_date -> pesan blok yang sama", cr.get("message"))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
