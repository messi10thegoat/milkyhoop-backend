"""BUG-207 uji DB di SALINAN (bukan prod): handler ASLI calculate Faktur/SO/Penawaran + penentu terima atas klon, tenant kaos + nyawit. Mutasi HANYA di klon (override izin uji).
A barang ber-WAC > 0: harga jual per satuan dasar < HPP -> peringatan JUAL_DI_BAWAH_HPP di /calculate Faktur, SO, Penawaran (pemilik = berizin: angka ada); di atas HPP -> tak ada
B pengguna non-pemilik tanpa REPORT R (override klon): pesan TANPA angka dan kunci angka ABSEN, butuh_konfirmasi true; tulis tak diblok (calculate tetap sukses)
C penerimaan: SQL rencana_terima asli (item_id + subtotal) atas PO klon; peringatan_terima tak galat; biaya per satuan dasar menyimpang > 30% dari HPP -> BIAYA_TERIMA_JANGGAL, 30% tepat -> tidak."""
import asyncio
import json
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from fastapi import Response
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


KUNCI_TAK_BERIZIN = {"code", "butuh_konfirmasi", "baris", "product_id", "nama", "pesan"}


def varian_angka(nilai: D) -> set:
    """Semua rupa NILAI (HPP / selisih %) yang tak boleh muncul bagi pemegang tanpa REPORT R: 35000.00, 35000, 35.000, Rp 35.000, 38.952,04, 50.00, ..."""
    n2 = nilai.quantize(D("0.01"))
    bulat = int(n2)
    ribuan = f"{bulat:,}".replace(",", ".")
    indo = f"{n2:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return {v for v in {str(n2), f"{n2:.2f}", str(bulat), ribuan, f"Rp {ribuan}", indo, f"Rp {indo}"} if len(v) >= 4}


def bocor(p: dict, angka: set) -> list:
    """Alasan kebocoran pada SATU objek peringatan tak berizin: kunci != persis KUNCI_TAK_BERIZIN, atau angka terlarang muncul di nilai mana pun (id dikecualikan agar tak positif palsu)."""
    alasan = []
    if set(p) != KUNCI_TAK_BERIZIN:
        alasan.append(f"kunci menyimpang: {sorted(set(p) ^ KUNCI_TAK_BERIZIN)}")
    teks = json.dumps({k: v for k, v in p.items() if k not in ("product_id", "baris")}, default=str, ensure_ascii=False)
    alasan += [f"angka {a_} muncul" for a_ in sorted(angka) if a_ in teks]
    return alasan


def per(x):
    v = x["peringatan"] if isinstance(x, dict) else x.peringatan
    return v


async def main():
    pool = await SI.get_pool()
    from backend.api_gateway.app.services import policy_engine_client as PEC  # singleton YANG SAMA dipakai middleware (impor MUTLAK; pola harness 199)
    PEC.init_policy_engine(pool)
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
            rb = None
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
                        selisih = (hpp - bawah) / hpp * 100
                        angka = varian_angka(hpp) | varian_angka(selisih) | varian_angka(bawah)  # HPP, selisih %, dan harga jual (selisih/HPP = margin/rugi)
                        alasan = bocor(p[0], angka) if len(p) == 1 else ["jumlah peringatan != 1"]
                        cek(f"B {tid} {nama} tak berizin: peringatan ada, butuh_konfirmasi, kunci PERSIS {sorted(KUNCI_TAK_BERIZIN)}, NILAI HPP/selisih tak muncul (semua format rupiah), hitungan sukses",
                            len(p) == 1 and p[0]["butuh_konfirmasi"] is True and alasan == [] and p[0]["pesan"] == f"Harga jual {p[0]['nama']} di bawah harga pokok barang.", f"{alasan} {p}")
                    # KONTROL MERAH harness (KM-1..KM-3): pemeriksa HARUS bisa gagal; tanpa ini hijau di atas tak berarti
                    p0 = dict(per(dok["Faktur"])[0])
                    km1 = bocor({**p0, "hpp": str(hpp.quantize(D("0.01")))}, angka)
                    km2 = bocor({**p0, "pesan": p0["pesan"] + " (HPP Rp " + f"{int(hpp):,}".replace(",", ".") + ")"}, angka)
                    km3 = bocor({**p0, "selisih_pct": str(selisih.quantize(D("0.01")))}, angka)
                    cek(f"B {tid} KM-1 kontrol merah: hpp disisipkan sebagai KUNCI -> terdeteksi", km1 != [], f"{km1}")
                    cek(f"B {tid} KM-2 kontrol merah: HPP disisipkan ke PESAN (format rupiah) -> terdeteksi", km2 != [], f"{km2}")
                    cek(f"B {tid} KM-3 kontrol merah: selisih_pct disisipkan sebagai kunci -> terdeteksi", km3 != [], f"{km3}")
                    cek(f"B {tid} KM-0 pemeriksa tak positif-palsu: objek bersih lolos", bocor(p0, angka) == [], f"{bocor(p0, angka)}")
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
            # D dokumen TERSIMPAN: SQL asli baris_tersimpan (kolom + urutan) atas dokumen klon tiap jenis
            for jenis, (hdr, items, fk, _urut) in JB.JENIS_TERSIMPAN.items():
                doc = await c.fetchval(f"/* tenant_id */ SELECT h.id FROM {hdr} h WHERE h.tenant_id = $1 AND EXISTS (SELECT 1 FROM {items} i WHERE i.{fk} = h.id AND i.jumlah_baris IS NOT NULL) ORDER BY h.created_at LIMIT 1", tid)
                if doc is None:
                    print(f"CATATAN D {tid}: tak ada dokumen {jenis} berbaris di klon; SQL tersimpan tak terukur", flush=True)
                    continue
                try:
                    baris = await JB.baris_tersimpan(c, tid, jenis, doc)
                    per_ = await JB.peringatan_tersimpan(c, tid, rq.state.user, jenis, doc)
                    cek(f"D {tid} {jenis} tersimpan: SQL asli memuat {len(baris)} baris bernilai, peringatan tanpa galat ({len(per_)})",
                        len(baris) >= 1 and all("nilai" in x and "item_id" in x for x in baris) and isinstance(per_, list))
                except Exception as e:  # noqa: BLE001
                    cek(f"D {tid} {jenis} tersimpan tanpa galat", False, f"{type(e).__name__}: {e}")
            # E dokumen TERSIMPAN NYATA (BUG-207 REOPEN): dokumen DIBUAT di klon lewat handler ASLI, lalu KETIGA pratinjau tersimpan dipanggil dgn SQL asli
            cust = await c.fetchval("/* tenant_id */ SELECT id FROM customers WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", tid)
            if cust is None:
                print(f"CATATAN E {tid}: tak ada pelanggan di klon; dokumen tersimpan tak bisa dibuat", flush=True)
            else:
                hari = date.today()

                def _id(r):
                    d = r["data"] if isinstance(r, dict) else getattr(r, "data", r)
                    return str(d["id"] if isinstance(d, dict) else d.id)

                async def buat(rq_, harga):
                    it_so = OS.SalesOrderItemCreate(description="uji207e", item_id=str(pid), quantity=D("2"), unit=dasar, unit_price=harga)
                    so = await SOR.create_sales_order(rq_, OS.CreateSalesOrderRequest(order_date=hari, customer_id=str(cust), customer_name="UJI207", items=[it_so]), Response())
                    qu = await QU.create_quote(rq_, QS.CreateQuoteRequest(quote_date=hari, customer_id=str(cust), customer_name="UJI207", items=[
                        QS.QuoteItemCreate(description="uji207e", item_id=str(pid), quantity=D("2"), unit=dasar, unit_price=harga)]), Response())
                    si = await SI.create_invoice(rq_, IS.CreateInvoiceRequest(customer_name="UJI207", invoice_date=hari, items=[
                        IS.InvoiceItemCreate(description="uji207e", item_id=str(pid), quantity=D("2"), unit=dasar, unit_price=harga)]), Response())
                    return _id(so), _id(qu), _id(si)

                async def lihat(rq_, ids):
                    so_id, qu_id, si_id = ids
                    return {"SO": (await SOR.preview_confirm_sales_order(rq_, so_id)).data["peringatan"],
                            "Penawaran": (await QU.preview_send_quote(rq_, qu_id))["data"]["peringatan"],
                            "Faktur": (await SI.preview_post_invoice(rq_, uuid.UUID(si_id), skip_deposit_ids=[]))["data"]["peringatan"]}
                try:
                    ids_bawah, ids_atas = await buat(rq, bawah), await buat(rq, atas)
                    pv_bawah, pv_atas = await lihat(rq, ids_bawah), await lihat(rq, ids_atas)
                    calc = await tiga_dokumen(rq, pid, dasar, bawah)
                    for nama in ("SO", "Penawaran", "Faktur"):
                        p = pv_bawah[nama]
                        c_ = per(calc[nama])
                        sama = len(p) == 1 and len(c_) == 1 and (p[0]["code"], p[0].get("harga_jual"), p[0].get("hpp"), p[0].get("selisih_pct")) == (
                            c_[0]["code"], c_[0].get("harga_jual"), c_[0].get("hpp"), c_[0].get("selisih_pct"))
                        cek(f"E {tid} {nama} TERSIMPAN (dokumen nyata di klon) harga {bawah} < HPP {hpp}: pratinjau tersimpan memuat 1 JUAL_DI_BAWAH_HPP berangka, SAMA dgn /calculate",
                            len(p) == 1 and p[0]["code"] == "JUAL_DI_BAWAH_HPP" and p[0]["butuh_konfirmasi"] is True and "hpp" in p[0] and sama, f"tersimpan={p} calculate={c_}")
                        cek(f"E {tid} {nama} TERSIMPAN harga {atas} > HPP: pratinjau tersimpan TANPA peringatan", pv_atas[nama] == [], f"{pv_atas[nama]}")
                    if rb is not None:
                        pv_rb = await lihat(rb, ids_bawah)
                        for nama in ("SO", "Penawaran", "Faktur"):
                            p = pv_rb[nama]
                            alasan = bocor(p[0], angka) if len(p) == 1 else ["jumlah peringatan != 1"]
                            cek(f"E {tid} {nama} TERSIMPAN tak berizin: peringatan ada, kunci PERSIS, NILAI HPP/selisih tak muncul", len(p) == 1 and alasan == [], f"{alasan} {p}")
                except Exception as e:  # noqa: BLE001
                    cek(f"E {tid} dokumen tersimpan nyata: buat + 3 pratinjau tanpa galat", False, f"{type(e).__name__}: {getattr(e, 'detail', e)}")
            tepat, lewat = (hpp * D("1.30")).quantize(D("0.0001")), (hpp * D("1.31")).quantize(D("0.0001"))
            m1 = await JB.hitung_terima(c, tid, [{"item_id": pid, "quantity": D("1"), "unit": dasar, "nilai": tepat}])
            m2 = await JB.hitung_terima(c, tid, [{"item_id": pid, "quantity": D("1"), "unit": dasar, "nilai": lewat}])
            cek(f"C {tid} biaya +30% tepat = tidak; +31% = BIAYA_TERIMA_JANGGAL", m1 == [] and len(m2) == 1 and m2[0]["kode"] == "BIAYA_TERIMA_JANGGAL", f"{m1} {m2}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
