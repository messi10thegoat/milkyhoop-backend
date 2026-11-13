"""BUG-196 uji DB di SALINAN (bukan prod): sum_total 13 daftar seragam SEBELUM (kode master dasar) vs SESUDAH (cabang), tenant kaos + nyawit. Mutasi HANYA di klon (prep).
Mode: `prep` (membuat baris batal/ditolak/kedaluwarsa uji di klon: SI void, SO cancelled, penawaran void/declined/kedaluwarsa, proforma expired) · `rekam` (panggil handler ASLI
tiap daftar dgn with_totals=true, cetak SUM|tenant|modul|sum_total|total + EXCL|tenant|modul|nilai = Σ baris tidak-berlaku dihitung dari TABEL (SQL mandiri)) · `bandingkan sebelum sesudah`:
modul TAK berubah (9) sesudah == sebelum; modul berubah (SI, SO, penawaran, proforma) sesudah == sebelum - EXCL persis; tiap modul berubah punya EXCL > 0 di >= 1 tenant (cakupan)."""
import asyncio
import inspect
import sys
import uuid

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
MODUL = [("expenses", "list_expenses"), ("sales_invoices", "list_invoices"), ("sales_orders", "list_sales_orders"), ("quotes", "list_quotes"),
         ("bills", "list_bills"), ("purchase_orders", "list_purchase_orders"), ("bill_payments", "list_bill_payments"), ("receive_payments", "list_receive_payments"),
         ("vendor_credits", "list_vendor_credits"), ("credit_notes", "list_credit_notes"), ("customer_deposits", "list_customer_deposits"),
         ("vendor_deposits", "list_vendor_deposits"), ("proformas", "list_proformas")]
BERUBAH = ("sales_invoices", "sales_orders", "quotes", "proformas")


async def req(c, tid):
    from starlette.requests import Request
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


def panggil(fn, request, **kw):
    """Panggil handler langsung dgn default Query/Header diurai (panggilan langsung = objek Query truthy, jadi tiap default dipaksa ke nilainya)."""
    args = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request":
            args[n] = request
        elif n in kw:
            args[n] = kw[n]
        else:
            d = p.default
            if d is inspect.Parameter.empty:
                args[n] = None
            elif hasattr(d, "default") and not isinstance(d, (str, int, float, bool, type(None))):
                args[n] = None if str(d.default) in ("PydanticUndefined", "Ellipsis") else d.default
            else:
                args[n] = d
    return fn(**args)


def ambil(res, kunci):
    if hasattr(res, "model_dump"):
        res = res.model_dump()
    if hasattr(res, "body"):
        import json
        res = json.loads(res.body)
    if isinstance(res, dict):
        if kunci in res:
            return res[kunci]
        if isinstance(res.get("data"), dict):
            return res["data"].get(kunci)
    return None


async def prep():
    from app.routers import expenses as EX
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        async def ubah(label, sql, *a):
            try:
                n = await c.execute(sql, *a)
                print(f"PREP {label}: {n}", flush=True)
            except Exception as e:
                print(f"PREP {label}: GAGAL {str(e)[:160]}", flush=True)
        for tid in (KA, NY):
            await ubah(f"{tid} SI draf->void", "UPDATE sales_invoices SET status = 'void' WHERE tenant_id = $1 AND id = (SELECT id FROM sales_invoices WHERE tenant_id = $1 AND status = 'draft' AND total_amount > 0 ORDER BY created_at LIMIT 1)", tid)
            await ubah(f"{tid} SO draf->cancelled", "UPDATE sales_orders SET status = 'cancelled' WHERE tenant_id = $1 AND id = (SELECT id FROM sales_orders WHERE tenant_id = $1 AND status = 'draft' AND total_amount > 0 ORDER BY created_at LIMIT 1)", tid)
            for st, ofs in (("void", 0), ("declined", 1)):
                await ubah(f"{tid} penawaran draf->{st}", f"UPDATE quotes SET status = '{st}' WHERE tenant_id = $1 AND id = (SELECT id FROM quotes WHERE tenant_id = $1 AND status = 'draft' AND total_amount > 0 ORDER BY created_at OFFSET {ofs} LIMIT 1)", tid)
            await ubah(f"{tid} penawaran draf->sent kedaluwarsa", "UPDATE quotes SET status = 'sent', expiry_date = CURRENT_DATE - 30 WHERE tenant_id = $1 AND id = (SELECT id FROM quotes WHERE tenant_id = $1 AND status = 'draft' AND total_amount > 0 ORDER BY created_at OFFSET 2 LIMIT 1)", tid)
            await ubah(f"{tid} proforma draf->expired", "UPDATE proformas SET status = 'expired' WHERE tenant_id = $1 AND id = (SELECT id FROM proformas WHERE tenant_id = $1 AND status = 'draft' ORDER BY created_at LIMIT 1)", tid)


async def rekam():
    from app.routers import expenses as EX
    from app.utils.tanggal_tenant import tanggal_dokumen
    import importlib
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            r = await req(c, tid)
            for modul, fn in MODUL:
                m = importlib.import_module(f"app.routers.{modul}")
                try:
                    res = await panggil(getattr(m, fn), r, with_totals=True, limit=1, per_page=1, skip=0, offset=0)
                    print(f"SUM|{tid}|{modul}|{ambil(res, 'sum_total')}|{ambil(res, 'total')}", flush=True)
                except Exception as e:
                    print(f"SUM|{tid}|{modul}|GALAT|{type(e).__name__}: {str(e)[:150]}", flush=True)
            hari = await tanggal_dokumen(c, tid)
            q = {"sales_invoices": ("SELECT COALESCE(SUM(total_amount), 0) FROM sales_invoices WHERE tenant_id = $1 AND status = 'void'", ()),
                 "sales_orders": ("SELECT COALESCE(SUM(total_amount), 0) FROM sales_orders WHERE tenant_id = $1 AND status = 'cancelled'", ()),
                 "quotes": ("SELECT COALESCE(SUM(total_amount), 0) FROM quotes WHERE tenant_id = $1 AND (status IN ('void', 'declined', 'expired') OR (status IN ('sent', 'viewed') AND expiry_date IS NOT NULL AND expiry_date < $2::date))", (hari,)),
                 "proformas": ("SELECT COALESCE(SUM(amount), 0) FROM proformas WHERE tenant_id = $1 AND status = 'expired'", ())}
            for modul, (sql, extra) in q.items():
                print(f"EXCL|{tid}|{modul}|{float(await c.fetchval(sql, tid, *extra))}", flush=True)


def baca(path):
    s, e = {}, {}
    for baris in open(path):
        p = baris.strip().split("|")
        if p[0] == "SUM":
            s[(p[1], p[2])] = p[3]
        elif p[0] == "EXCL":
            e[(p[1], p[2])] = float(p[3])
    return s, e


def bandingkan(sblm, ssdh):
    sb, _ = baca(sblm)
    sa, ex = baca(ssdh)
    hasil = []

    def cek(nama, ok, info=""):
        hasil.append(bool(ok))
        print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)
    for tid in (KA, NY):
        for modul, _ in MODUL:
            b, a = sb.get((tid, modul)), sa.get((tid, modul))
            if b is None or a is None or "GALAT" in (b, a) or "None" in (b, a):
                cek(f"{tid} {modul}: sum_total terbaca", False, f"sebelum={b} sesudah={a}")
                continue
            b, a = float(b), float(a)
            if modul in BERUBAH:
                cek(f"{tid} {modul}: sesudah == sebelum - baris tak-berlaku (persis)", abs(a - (b - ex[(tid, modul)])) < 0.01, f"sebelum={b} sesudah={a} excl={ex[(tid, modul)]}")
            else:
                cek(f"{tid} {modul}: TAK berubah (sesudah == sebelum)", abs(a - b) < 0.01, f"sebelum={b} sesudah={a}")
    for modul in BERUBAH:
        cek(f"cakupan {modul}: ada baris tak-berlaku (excl > 0) di >= 1 tenant", any(ex.get((t, modul), 0) > 0 for t in (KA, NY)), str({t: ex.get((t, modul)) for t in (KA, NY)}))
    print(f"HASIL: {sum(hasil)}/{len(hasil)} PASS", flush=True)
    sys.exit(0 if all(hasil) else 1)


if __name__ == "__main__":
    if sys.argv[1] == "prep":
        sys.path.insert(0, "/app/backend/api_gateway")
        asyncio.run(prep())
    elif sys.argv[1] == "rekam":
        sys.path.insert(0, "/app/backend/api_gateway")
        asyncio.run(rekam())
    else:
        bandingkan(sys.argv[2], sys.argv[3])
