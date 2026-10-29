"""Harness NYATA ND-BUAT (kaos SAJA, penanda 'TES E2E'): SATU transaksi luar pada satu koneksi SELALU di-ROLLBACK (pola AS-GP / B1b-2: get_pool
tertambal, transaksi dalam = savepoint; readonly pratinjau tak ditegakkan DB di savepoint -- dikunci tes unit). Sesudah ROLLBACK diukur di koneksi
LAIN: nol sisa dokumen TES E2E.

Di DALAM transaksi (ikut di-ROLLBACK): "Tenant".is_pkp kaos = true supaya PPN Masukan bisa dijurnal (kaos non-PKP).
Syarat MASTER: ND 0% (boleh) · ND 11% (boleh) · ND 12% (DIBLOK ND_PAJAK_DPP_BELUM_DIDUKUNG, nol tulis) · dari-FPB atas FPB 11% kepala ber-diskon
dokumen yang dibuat+diposting di sini: (a) ND PENUH total == FPB total PERSIS (selisih 0 sen); (b) ND dari FPB diposting: jurnal == pratinjau,
pembalik PPN Masukan == porsi PPN FPB (penuh = PPN FPB). Kontrol: dari-FPB atas FPB fixture tak konsisten (ZSEARCHBILL-1: subtotal 0, amount 10001)
WAJIB diblok ND_TOTAL_BEDA_TAGIHAN. Semua kanal keluar (HTTP/SMTP) di-stub: WAJIB 0 panggilan. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

KELUAR = []


def _pasang_pagar_keluar():
    import smtplib

    def catat(nama):
        def f(*a, **k):
            KELUAR.append(nama)
            raise RuntimeError(f"harness: kanal keluar {nama} diblokir")
        return f

    async def acatat(*a, **k):
        KELUAR.append("async-http")
        raise RuntimeError("harness: kanal keluar async-http diblokir")
    smtplib.SMTP.__init__ = catat("smtp")
    smtplib.SMTP_SSL.__init__ = catat("smtp_ssl")
    for modul, kelas, attr, f in (("httpx", "Client", "send", catat("httpx")), ("httpx", "AsyncClient", "send", acatat),
                                  ("requests", "Session", "send", catat("requests")), ("aiohttp", "ClientSession", "_request", acatat)):
        try:
            setattr(getattr(__import__(modul), kelas), attr, f)
        except ImportError:
            pass


_pasang_pagar_keluar()

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.routers import bills as RB  # noqa: E402
from app.routers import vendor_credits as VC  # noqa: E402
from app.schemas import vendor_credits as VS  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

T = "kaos-biru-konveksi"
VENDOR, BARANG = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"  # == AS-GP2
PENANDA = "TES E2E ND-BUAT"
D = Decimal


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}
    return r


def dt(x):
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else x


async def main(keluar):
    from app.schemas.bills import CreateBillRequestV2 as CB
    c = await asyncpg.connect(os.environ["URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    vnama = await c.fetchval("SELECT name FROM vendors WHERE id = $1 AND tenant_id = $2", uuid.UUID(VENDOR), T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()

    class CM:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()

    async def gp(): return Pool()
    for mod in (RB, VC):
        mod.get_pool = gp
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def n_nd():
        return await c.fetchval("SELECT count(*) FROM vendor_credits WHERE tenant_id = $1", T)

    async def pv(**badan):
        return (await VC.preview_create_vendor_credit(req(uid), VC.VendorCreditPreviewRequest(**badan)))["data"]

    async def buat(badan):
        sp = c.transaction()
        await sp.start()
        try:
            r = await VC.create_vendor_credit(req(uid), VS.CreateVendorCreditRequest(**badan))
            await sp.commit()
            return r, None
        except HTTPException as e:
            await sp.rollback()
            return None, e

    c2 = await asyncpg.connect(os.environ["URL"])  # koneksi LAIN: saksi is_pkp kaos (syarat MASTER: sebelum / selama / sesudah = false)
    pkp_lain = {"sebelum": await c2.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T)}
    luar = c.transaction()
    await luar.start()
    try:
        await c.execute('UPDATE "Tenant" SET is_pkp = true WHERE id = $1', T)  # di dalam ROLLBACK
        pkp_lain["selama"] = await c2.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T)
        pkp_lain["selama_tx_ini"] = await c.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T)
        hari = await tanggal_dokumen(c, T)
        dasar = {"vendor_id": VENDOR, "vendor_name": vnama, "vendor_credit_date": hari.isoformat(), "reason": "return", "notes": PENANDA}

        # --- ND manual 0% / 11% / 12%
        for nama, tarif, boleh in (("0%", 0, True), ("11%", 11, True), ("12%", 12, False)):
            b = {**dasar, "items": [{"description": f"{PENANDA} {nama}", "quantity": 2, "unit_price": "1500.50", "tax_rate": tarif}]}
            d = await pv(**b)
            kode = [x["code"] for x in d["blocks"]]
            sebelum = await n_nd()
            r, e = await buat(b)
            if boleh:
                harus(f"ND manual {nama}: pratinjau boleh + create draf (+1 ND)", d["can_proceed"] and r and r["data"]["status"] == "draft"
                      and await n_nd() == sebelum + 1, (kode, e and e.detail))
                harus(f"ND manual {nama}: angka pratinjau == tersimpan (sen terjaga)",
                      D(str(d["total_amount"])) == D(str(await c.fetchval("SELECT total_amount FROM vendor_credits WHERE id = $1",
                                                                          uuid.UUID(r["data"]["id"])))), d["total_amount"])
            else:
                harus(f"ND manual {nama}: pratinjau DIBLOK ND_PAJAK_DPP_BELUM_DIDUKUNG", kode == ["ND_PAJAK_DPP_BELUM_DIDUKUNG"], kode)
                harus(f"ND manual {nama}: create 400 berkode, NOL tulis", e is not None and e.status_code == 400
                      and e.detail.get("code") == "ND_PAJAK_DPP_BELUM_DIDUKUNG" and await n_nd() == sebelum, (e and e.detail, await n_nd(), sebelum))

        # --- FPB 11% kepala ber-diskon dokumen: buat + posting di sini
        nomor = f"TES-E2E-NDB-{uuid.uuid4().hex[:6]}"
        sp = c.transaction()
        await sp.start()
        try:
            await RB.create_bill_v2(req(uid), CB(vendor_id=VENDOR, invoice_number=nomor, due_date=hari + timedelta(days=30), notes=PENANDA,
                                                 tax_rate=11, invoice_discount_amount=2000,
                                                 items=[{"product_id": BARANG, "product_name": "TES E2E kain", "qty": 3, "price": 10000},
                                                        {"product_id": BARANG, "product_name": "TES E2E benang", "qty": 2, "price": 5000}]))
            bid = await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
            await RB.post_bill(req(uid), bid)
            await sp.commit()
        except Exception as e:  # noqa: BLE001
            await sp.rollback()
            harus("FPB 11% dibuat + diposting", False, repr(e)[:300])
            raise
        fpb = await c.fetchrow("SELECT status_v2, subtotal, invoice_discount_total, tax_amount, amount FROM bills WHERE id = $1", bid)
        catat["fpb"] = {k: str(v) for k, v in dict(fpb).items()}
        harus("FPB 11% posted", fpb["status_v2"] == "posted", dict(fpb))
        baris = await c.fetch("SELECT id, quantity FROM bill_items WHERE bill_id = $1 ORDER BY line_number", bid)

        # (a) ND PENUH == FPB persis
        d = await pv(original_bill_id=str(bid), reason="return", notes=PENANDA)
        catat["penuh"] = {k: d[k] for k in ("subtotal", "discount_amount", "tax_amount", "total_amount", "blocks")}
        harus("(a) dari-FPB PENUH boleh", d["can_proceed"], d["blocks"])
        harus("(a) total ND penuh == total FPB (selisih 0 sen)", d["total_amount"] is not None and D(str(d["total_amount"])) == D(str(fpb["amount"])),
              (d["total_amount"], str(fpb["amount"])))
        harus("(a) PPN ND penuh == PPN FPB", d["tax_amount"] is not None and D(str(d["tax_amount"])) == D(str(fpb["tax_amount"])),
              (d["tax_amount"], str(fpb["tax_amount"])))

        # sebagian: baris 1 qty 1 -> PPN == porsi PPN FPB (neto terpilih / neto semua)
        dp = await pv(original_bill_id=str(bid), reason="return", lines=[{"original_bill_item_id": str(baris[0]["id"]), "quantity": 1}])
        porsi = (D(str(fpb["tax_amount"])) * D("10000") / D("40000")).quantize(D("0.01"))
        catat["sebagian"] = {"tax": dp["tax_amount"], "porsi_ppn_fpb": str(porsi), "total": dp["total_amount"]}
        harus("dari-FPB SEBAGIAN: PPN == porsi PPN FPB (neto 10.000/40.000)", dp["can_proceed"] and D(str(dp["tax_amount"])) == porsi,
              catat["sebagian"])

        # (b) ND dari FPB PENUH diposting: jurnal == pratinjau; PPN Masukan dibalik == PPN FPB
        r, e = await buat(json.loads(json.dumps(d["payload"])))
        harus("(b) create dari payload pratinjau", r is not None, e and e.detail)
        ndid = uuid.UUID(r["data"]["id"])
        stok_awal = await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
                                     T, uuid.UUID(BARANG))
        sp = c.transaction()
        await sp.start()
        try:
            await VC.post_vendor_credit(req(uid), ndid)
            await sp.commit()
        except Exception as ex:  # noqa: BLE001
            await sp.rollback()
            harus("(b) posting ND dari FPB", False, repr(getattr(ex, "detail", ex))[:300])
            raise
        rows = await c.fetch("""SELECT ca.account_code, jl.debit, jl.credit, je.source_type FROM journal_entries je
                                JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts ca ON ca.id = jl.account_id
                                WHERE je.tenant_id = $1 AND je.source_id = $2 AND je.status = 'POSTED'""", T, str(ndid))
        nyata = sorted((x["account_code"], D(str(x["debit"])), D(str(x["credit"]))) for x in rows if x["source_type"] == "VENDOR_CREDIT")
        rencana = sorted((ln["account_code"], D(str(ln["debit"])), D(str(ln["credit"]))) for j in d["journals"]
                         if j["source_type"] == "VENDOR_CREDIT" for ln in j["lines"])
        catat["jurnal"] = {"nyata": [list(map(str, x)) for x in nyata], "pratinjau": [list(map(str, x)) for x in rencana]}
        harus("(b) jurnal ND terposting == pratinjau", nyata and nyata == rencana, catat["jurnal"])
        # akun PPN = baris pratinjau bermemo "PPN Retur" (baris_posting_nd); nilainya diambil dari jurnal TERPOSTING di akun itu
        akun_ppn = {ln["account_code"] for j in d["journals"] for ln in j["lines"] if (ln.get("memo") or "").startswith("PPN Retur")}
        ppn_nd = sum((x[2] for x in nyata if x[0] in akun_ppn), D("0"))
        harus("(b) pembalik PPN Masukan (kredit akun PPN terposting) == PPN FPB",
              len(akun_ppn) == 1 and ppn_nd == D(str(fpb["tax_amount"])) and ppn_nd > 0, (sorted(akun_ppn), str(ppn_nd), str(fpb["tax_amount"])))

        # COGS-NOMOR-PER-BARIS: 2 baris barang dilacak -> 2 jurnal HPP bernomor unik (kedua berakhiran -2)
        hpp = [x["journal_number"] for x in await c.fetch(
            """SELECT journal_number FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_CREDIT_COGS' AND source_id = $2
               AND reversal_of_id IS NULL ORDER BY created_at""", T, ndid)]
        nd_no = await c.fetchval("SELECT credit_number FROM vendor_credits WHERE id = $1", ndid)
        catat["hpp"] = hpp
        harus("posting ND 2 baris dilacak: 2 jurnal HPP bernomor unik (COGS-<nd>, COGS-<nd>-2)", hpp == [f"COGS-{nd_no}", f"COGS-{nd_no}-2"], hpp)

        # VOID ND: SEMUA jurnal ND (VENDOR_CREDIT + KEDUA HPP) dibalik -> net 0 per akun TERMASUK Persediaan/HPP; kartu stok kembali ke awal
        sp = c.transaction()
        await sp.start()
        try:
            await VC.void_vendor_credit(req(uid), ndid, VS.VoidVendorCreditRequest(reason=f"{PENANDA} void"))
            await sp.commit()
        except Exception as ex:  # noqa: BLE001
            await sp.rollback()
            harus("void ND 2 baris", False, repr(getattr(ex, "detail", ex))[:300])
            raise
        per_akun = await c.fetch("""SELECT ca.account_code, SUM(jl.debit) - SUM(jl.credit) AS net, count(DISTINCT je.id) AS n_jurnal
                                    FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                                    JOIN chart_of_accounts ca ON ca.id = jl.account_id
                                    WHERE je.tenant_id = $1 AND je.source_id = $2 AND je.status = 'POSTED' GROUP BY 1 ORDER BY 1""", T, ndid)
        catat["void_per_akun"] = [(x["account_code"], str(x["net"]), x["n_jurnal"]) for x in per_akun]
        harus("void ND: net 0 PER AKUN atas semua jurnal ND (incl. Persediaan/HPP)", per_akun and all(D(str(x["net"])) == 0 for x in per_akun),
              catat["void_per_akun"])
        hpp_balik = await c.fetchval("""SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_CREDIT_COGS' AND source_id = $2
                                        AND reversal_of_id IS NULL AND reversed_by_id IS NOT NULL""", T, ndid)
        harus("void ND: KEDUA jurnal HPP dibalik", hpp_balik == 2, hpp_balik)
        stok_akhir = await c.fetchval("SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
                                      T, uuid.UUID(BARANG))
        ledger_nd = await c.fetchval("""SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1
                                        AND source_id = $2""", T, ndid)
        catat["stok"] = {"awal": str(stok_awal), "akhir": str(stok_akhir), "net_ledger_nd": str(ledger_nd)}
        harus("void ND: kartu stok kembali ke awal (net ledger ND = 0)", D(str(stok_akhir)) == D(str(stok_awal)) and D(str(ledger_nd)) == 0,
              catat["stok"])

        # kontrol: fixture tak konsisten -> blok ND_TOTAL_BEDA_TAGIHAN (penjaga bisa merah)
        z = await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = 'ZSEARCHBILL-1'", T)
        if z:
            dz = await pv(original_bill_id=str(z), reason="return")
            harus("kontrol: FPB tak konsisten (ZSEARCHBILL-1) -> ND_TOTAL_BEDA_TAGIHAN", "ND_TOTAL_BEDA_TAGIHAN" in [x["code"] for x in dz["blocks"]],
                  dz["blocks"])
        else:
            harus("kontrol: fixture ZSEARCHBILL-1 ada", False, "tak ada")
    finally:
        await luar.rollback()
    sisa = {"nd": await c.fetchval("SELECT count(*) FROM vendor_credits WHERE tenant_id = $1 AND notes LIKE $2", T, f"{PENANDA}%"),
            "fpb": await c.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND notes LIKE $2", T, f"{PENANDA}%"),
            "pkp": await c.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T)}
    harus("ROLLBACK: nol sisa ND/FPB TES E2E + is_pkp kaos kembali false", sisa["nd"] == 0 and sisa["fpb"] == 0 and sisa["pkp"] is False, sisa)
    pkp_lain["sesudah"] = await c2.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T)
    await c2.close()
    catat["is_pkp_koneksi_lain"] = pkp_lain
    harus("is_pkp kaos dari KONEKSI LAIN: sebelum/selama/sesudah = false (selama di tx ini = true -> UPDATE nyata tapi tak ter-commit)",
          pkp_lain["sebelum"] is False and pkp_lain["selama"] is False and pkp_lain["sesudah"] is False and pkp_lain["selama_tx_ini"] is True,
          pkp_lain)
    harus("nol panggilan kanal keluar (surel/WA/HTTP)", KELUAR == [], KELUAR)
    await c.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
