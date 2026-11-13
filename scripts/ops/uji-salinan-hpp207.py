"""BUG-207 uji DB di SALINAN (bukan prod): handler ASLI calculate Faktur/SO/Penawaran + penentu terima atas klon, tenant kaos + nyawit. Mutasi HANYA di klon (override izin uji).
A barang ber-WAC > 0: harga jual per satuan dasar < HPP -> peringatan JUAL_DI_BAWAH_HPP di /calculate Faktur, SO, Penawaran (pemilik = berizin: angka ada); di atas HPP -> tak ada
B pengguna non-pemilik tanpa REPORT R (override klon): pesan TANPA angka dan kunci angka ABSEN, butuh_konfirmasi true; tulis tak diblok (calculate tetap sukses)
C penerimaan: SQL rencana_terima asli (item_id + subtotal) atas PO klon; peringatan_terima tak galat; biaya per satuan dasar menyimpang > 30% dari HPP -> BIAYA_TERIMA_JANGGAL, 30% tepat -> tidak."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as PO  # noqa: E402,F401
from app.routers import quotes as QU  # noqa: E402
from app.routers import sales_invoices as SI  # noqa: E402
from app.routers import sales_orders as SOR  # noqa: E402
from app.schemas import quotes as QS  # noqa: E402
from app.schemas import sales_invoices as IS  # noqa: E402
from app.schemas import sales_orders as OS  # noqa: E402
from app.services import jual_bawah_hpp as JB  # noqa: E402
from app.services import po_rencana as PR  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []
SQL_BARANG = """/* tenant_id */ SELECT p.id, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS dasar FROM products p
JOIN LATERAL (SELECT average_cost FROM inventory_ledger WHERE tenant_id = p.tenant_id AND product_id = p.id ORDER BY created_at DESC LIMIT 1) l ON true
WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND l.average_cost > 0 ORDER BY p.created_at LIMIT 1"""


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, role="OWNER"):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": role}
    return r


async def tiga_dokumen(rq, pid, dasar, harga, qty=D("2")):
    """/calculate Faktur, SO, Penawaran dengan satu baris barang; -> {nama: data}."""
    hari, cust = date.today(), str(uuid.uuid4())
    si = await SI.calculate_invoice(rq, IS.CreateInvoiceRequest(customer_name="UJI207", invoice_date=hari, items=[
        IS.InvoiceItemCreate(description="uji207", item_id=str(pid), quantity=qty, unit=dasar, unit_price=harga)]))
    so = await SOR.calculate_sales_order(rq, OS.CreateSalesOrderRequest(order_date=hari, customer_id=cust, customer_name="UJI207", items=[
        OS.SalesOrderItemCreate(description="uji207", item_id=str(pid), quantity=qty, unit=dasar, unit_price=harga)]))
    qu = await QU.calculate_quote(rq, QS.CreateQuoteRequest(quote_date=hari, customer_id=cust, customer_name="UJI207", items=[
        QS.QuoteItemCreate(description="uji207", item_id=str(pid), quantity=qty, unit=dasar, unit_price=harga)]))
    d = lambda x: x["data"] if isinstance(x, dict) else x.data  # noqa: E731
    return {"Faktur": d(si), "SO": d(so), "Penawaran": d(qu)}


def per(x):
    v = x["peringatan"] if isinstance(x, dict) else x.peringatan
    return v


async def main():
    pool = await SI.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            b = await c.fetchrow(SQL_BARANG, tid)
            cek(f"prasyarat {tid}: pemilik, barang goods ber-WAC > 0", uid is not None and b is not None)
            if uid is None or b is None:
                continue
            pid, dasar = b["id"], b["dasar"]
            hpp = (await JB._hpp_barang(c, tid, [pid]))[str(pid)]["hpp"]
            rq = req(tid, uid)
            bawah, atas = (hpp / 2).quantize(D("0.01")), (hpp * 2).quantize(D("0.01"))
            # A pemilik (berizin REPORT R)
            cek(f"A {tid} pemilik boleh lihat HPP (REPORT R)", await JB.boleh_lihat_hpp(rq.state.user) is True)
            try:
                dok = await tiga_dokumen(rq, pid, dasar, bawah)
                for nama, data in dok.items():
                    p = per(data)
                    cek(f"A {tid} {nama} /calculate harga {bawah} < HPP {hpp}: 1 peringatan JUAL_DI_BAWAH_HPP berangka, butuh_konfirmasi, hitungan sukses",
                        len(p) == 1 and p[0]["code"] == "JUAL_DI_BAWAH_HPP" and p[0]["butuh_konfirmasi"] is True and "harga_jual" in p[0] and "hpp" in p[0] and "selisih_pct" in p[0]
                        and D(p[0]["hpp"]) == hpp.quantize(D("0.01")), f"{p}")
                dok2 = await tiga_dokumen(rq, pid, dasar, atas)
                for nama, data in dok2.items():
                    cek(f"A {tid} {nama} /calculate harga {atas} > HPP: tanpa peringatan", per(data) == [], f"{per(data)}")
            except Exception as e:  # noqa: BLE001
                cek(f"A {tid} /calculate tiga dokumen tanpa galat", False, f"{type(e).__name__}: {e}")
            # B non-pemilik tanpa REPORT R (override klon)
            biasa = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code <> 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            if biasa is None:
                print(f"CATATAN B {tid}: tak ada anggota non-pemilik di klon; kasus tak berizin hanya diuji unit", flush=True)
            else:
                await c.execute("INSERT INTO user_permission_overrides (user_id, tenant_id, module, actions) VALUES ($1, $2, 'REPORT', '{}') "
                                "ON CONFLICT (user_id, tenant_id, module) DO UPDATE SET actions = '{}'", str(biasa), tid)
                rb = req(tid, biasa, "USER")
                cek(f"B {tid} non-pemilik dengan override REPORT kosong: TIDAK boleh lihat HPP", await JB.boleh_lihat_hpp(rb.state.user) is False)
                try:
                    dok = await tiga_dokumen(rb, pid, dasar, bawah)
                    for nama, data in dok.items():
                        p = per(data)
                        cek(f"B {tid} {nama} tak berizin: peringatan ada, TANPA angka (kunci angka ABSEN), hitungan tetap sukses",
                            len(p) == 1 and p[0]["butuh_konfirmasi"] is True and set(p[0]) == {"code", "butuh_konfirmasi", "baris", "product_id", "nama", "pesan"}
                            and not any(ch.isdigit() for ch in p[0]["pesan"]), f"{p}")
                except Exception as e:  # noqa: BLE001
                    cek(f"B {tid} /calculate tak berizin tanpa galat", False, f"{type(e).__name__}: {e}")
            # C penerimaan
            po = await c.fetchrow("/* tenant_id */ SELECT p.* FROM purchase_orders p WHERE p.tenant_id = $1 AND EXISTS (SELECT 1 FROM purchase_order_items i WHERE i.po_id = p.id AND i.item_id IS NOT NULL) ORDER BY p.created_at LIMIT 1", tid)
            if po is None:
                print(f"CATATAN C {tid}: tak ada PO berbaris barang di klon; SQL rencana_terima tak terukur", flush=True)
            else:
                async with c.transaction(readonly=True):
                    r = await PR.rencana_terima(c, po, None, None, date.today(), kunci=False)
                    kolom = bool(r["semua"]) and all("item_id" in x.keys() and "subtotal" in x.keys() for x in r["semua"])
                    cek(f"C {tid} SQL rencana_terima asli memuat item_id + subtotal ({len(r['semua'])} baris)", kolom)
                    try:
                        hasil = await JB.peringatan_terima(c, tid, rq.state.user, r)
                        cek(f"C {tid} peringatan_terima atas PO klon tanpa galat (hasil {len(hasil)} peringatan)", isinstance(hasil, list))
                    except Exception as e:  # noqa: BLE001
                        cek(f"C {tid} peringatan_terima tanpa galat", False, f"{type(e).__name__}: {e}")
            tepat, lewat = (hpp * D("1.30")).quantize(D("0.0001")), (hpp * D("1.31")).quantize(D("0.0001"))
            m1 = await JB.hitung_terima(c, tid, [{"item_id": pid, "quantity": D("1"), "unit": dasar, "nilai": tepat}])
            m2 = await JB.hitung_terima(c, tid, [{"item_id": pid, "quantity": D("1"), "unit": dasar, "nilai": lewat}])
            cek(f"C {tid} biaya +30% tepat = tidak; +31% = BIAYA_TERIMA_JANGGAL", m1 == [] and len(m2) == 1 and m2[0]["kode"] == "BIAYA_TERIMA_JANGGAL", f"{m1} {m2}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
