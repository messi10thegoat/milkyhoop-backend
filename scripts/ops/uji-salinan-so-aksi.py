"""aksi_tersedia DETAIL SO (gelombang 3 J1a) -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA (master) dan BARU.
Tulis HANYA di salinan, dan setiap penulis dijalankan di transaksi LUAR yang di-ROLLBACK (klon tetap utuh antar-langkah).
 A (dua pohon) GET detail SO sampel (per tenant x status <= SAMPEL): baru = lama + aksi_tersedia (medan lama identik).
 W (dua pohon) penulis tiap aksi (konfirmasi, ubah-pratinjau, batal, hapus, to-invoice, kirim/SJ, pelunasan-pratinjau, tutup, terbit
   faktur draf, fase lokasi, SPK) + pratinjau buat DP & proforma: hasil (ok / status+detail) lama == baru -> perilaku TULIS identik
   (termasuk tutup kunci=True dan penjaga yang diekstrak).
 P (baru) paritas: tersedia=false => penulis menolak dgn kalimat == alasan; tersedia=true => penulis tak menolak dgn blok KEADAAN.
 K (baru) kunci yang DIPEGANG transaksi READ ONLY aksi_tersedia di akhirnya (pg_locks pid sendiri): nol advisory/RowShare/RowExclusive.
 T (dua pohon) waktu GET detail (3 putaran, p50/p95) -> banding."""
import asyncio
import json
import os
import sys
import time
from datetime import date

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import sales_orders as SO  # noqa: E402
from app.routers import kode_order as KOR, sj_so as SJR, so_fase_lokasi as FLR, sales_invoices as SI  # noqa: E402
from app.routers import customer_deposits as CD, proformas as PF  # noqa: E402
from app.schemas.sales_orders import (CancelSalesOrderRequest, CloseSalesOrderRequest, UpdateSalesOrderRequest)  # noqa: E402
from app.services.so_pengiriman import SOFulfillRequest  # noqa: E402
from app.services.so_pelunasan import SOReceivePaymentPreviewRequest  # noqa: E402
from app.schemas.customer_deposits import CreateCustomerDepositRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
SAMPEL = int(os.environ.get("SAMPEL", "15"))
BARU = hasattr(SO, "aksi_tersedia_so")
MOD = (SO, KOR, SJR, FLR, SI, CD, PF)
ASLI = {(m, n): getattr(m, n) for m in MOD for n in ("get_pool", "get_db_pool", "_pool") if hasattr(m, n)}
CEK, KUNCI = [], []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])


def req(tid, method="POST"):
    r = Request({"type": "http", "method": method, "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-so-aksi",
                    "role": "OWNER"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


def pesan(d):
    return str(d.get("message") or d.get("detail") or d.get("code")) if isinstance(d, dict) else str(d)


async def coba(f):
    try:
        return {"ok": True, "hasil": j(await f)}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": j(e.detail), "pesan": pesan(e.detail)}
    except Exception as e:  # noqa: BLE001 -- 500 penulis dicatat, bukan dibuang
        return {"ok": False, "status": 500, "detail": repr(e)[:300], "pesan": repr(e)[:300]}


class Kolam:
    """Pool palsu: semua handler memakai SATU koneksi yang sudah di dalam transaksi luar (di-rollback pemanggil)."""
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class Cm:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return Cm()

    def __getattr__(self, n):
        return getattr(self.c, n)


def pasang(kolam):
    for (m, n) in ASLI:
        async def palsu(*a, _k=kolam, **k):
            return _k
        setattr(m, n, palsu)


def lepas():
    for (m, n), f in ASLI.items():
        setattr(m, n, f)


async def tulis(conn, f):
    """Penulis di transaksi LUAR yang SELALU di-rollback (klon tak berubah antar-langkah)."""
    tr = conn.transaction()
    await tr.start()
    pasang(Kolam(conn))
    try:
        return await coba(f())
    finally:
        lepas()
        await tr.rollback()


async def penulis(conn, tid, so, akun_kas, gudang):
    oid = str(so["id"])
    r = lambda: req(tid)  # noqa: E731
    w = {}
    w["konfirmasi"] = await tulis(conn, lambda: SO.confirm_sales_order(r(), oid))
    w["ubahkepala"] = await tulis(conn, lambda: SO.preview_edit_sales_order(r(), oid, UpdateSalesOrderRequest()))
    w["batal"] = await tulis(conn, lambda: SO.cancel_sales_order(r(), oid, CancelSalesOrderRequest(reason="UJI aksi")))
    w["hapus"] = await tulis(conn, lambda: SO.delete_sales_order(r(), oid))
    w["faktur"] = await tulis(conn, lambda: SO.convert_to_invoice(r(), Response(), oid, None))
    w["kirim"] = await tulis(conn, lambda: SO.fulfill_order(r(), Response(), oid, SOFulfillRequest()))
    w["kirim_sj"] = await tulis(conn, lambda: SJR.create_sj_so(r(), Response(), oid, None))
    w["bayar"] = await tulis(conn, lambda: SO.preview_receive_payment_from_order(r(), oid, SOReceivePaymentPreviewRequest()))
    w["tutup"] = await tulis(conn, lambda: SO.close_sales_order(r(), oid, CloseSalesOrderRequest(reason="UJI aksi")))
    w["spk"] = await tulis(conn, lambda: KOR.terbitkan_spk(r(), oid))
    w["fase"] = await tulis(conn, lambda: FLR.fase_lokasi_ubah(r(), oid, FLR.FaseLokasiRequest(fase="tersedia", warehouse_id=gudang)))
    draf = await conn.fetchval("SELECT id FROM sales_invoices WHERE tenant_id = $1 AND sales_order_id = $2 AND status = 'draft' "
                               "ORDER BY invoice_date, invoice_number LIMIT 1", tid, so["id"])
    w["terbitfaktur"] = (await tulis(conn, lambda: SI.post_invoice(r(), draf, None, Response()))) if draf else {"ok": False, "tanpa_draf": True}
    # pratinjau READ ONLY (penentu SAMA dengan penulis) lewat pool ASLI: badan nominal terkecil (aksi menilai status/sisa saja)
    w["dp"] = await coba(CD.preview_create_customer_deposit(r(), CreateCustomerDepositRequest(
        customer_id=str(so["customer_id"]) if so["customer_id"] else None, customer_name=so["customer_name"] or "UJI", amount=1,
        deposit_date=date.today(), payment_method="cash", account_id=str(akun_kas), sales_order_id=oid)))
    w["proforma"] = await coba(PF.preview_create_proforma(r(), PF.CreateProformaRequest(sales_order_id=oid, amount=1)))
    return w


def ringkas_tulis(w):
    """Hasil penulis yang dibandingkan lama vs baru: ok + (status, detail) penolakan; hasil sukses = ok saja (nomor/id baru tiap jalan)."""
    out = {}
    for k, v in w.items():
        if k in ("dp", "proforma", "bayar"):  # pratinjau: blok (kode + kalimat) dibandingkan utuh
            d = (v.get("hasil") or {}).get("data") or {}
            out[k] = {"ok": v["ok"], "blocks": [[b.get("code"), b.get("message")] for b in d.get("blocks", [])], "tolak": v.get("detail")}
        else:
            out[k] = {"ok": v["ok"], "status": v.get("status"), "detail": v.get("detail"), "tanpa_draf": v.get("tanpa_draf")}
    return out


def blok_pratinjau(v):
    d = (v.get("hasil") or {}).get("data") or {}
    return d.get("blocks", [])


def paritas(tid, so, at, w):
    """tersedia=false => penulis menolak dgn kalimat == alasan; tersedia=true => tidak ditolak dengan blok KEADAAN aksi itu."""
    nomor = f"{tid}:{so['order_number']}:{so['status']}"
    lolos_isian = []

    def tolak(v):
        return None if v["ok"] else v.get("pesan")

    for k in SO.AKSI_DETAIL_SO:
        a = at[k]
        if k in ("fasekirim", "fasetersedia", "faseubah"):
            v = w["fase"]
            t = tolak(v)
            ok = (t == a["alasan"]) if not a["tersedia"] else (v["ok"] or (v["detail"] or {}).get("code") not in SO.KEADAAN_FASE)
        elif k == "kirim":
            f_kd = [b for b in blok_tolak(w["kirim"]) if b.get("code") in SO.KEADAAN_KIRIM]
            sj_tutup = pesan_kode(w["kirim_sj"]) in SO.KEADAAN_KIRIM_SJ + ("SJ_SO_NONAKTIF",)
            ok = (bool(f_kd) and sj_tutup and a["alasan"] in [f_kd[0]["message"], tolak(w["kirim_sj"])]) if not a["tersedia"] else \
                not (f_kd and sj_tutup)
        elif k == "bayar":
            bk = [b for b in blok_pratinjau(w["bayar"]) if b.get("code") in SO.KEADAAN_BAYAR]
            ok = (bool(bk) and bk[0]["message"] == a["alasan"]) if not a["tersedia"] else (w["bayar"]["ok"] and not bk)
        elif k == "dp":
            bk = [b for b in blok_pratinjau(w["dp"]) if b.get("code") in ("SO_NOT_ACCEPTING_DEPOSIT", "PLAFON_SO_TERLAMPAUI")]
            ok = (bool(bk) and (bk[0]["message"] == a["alasan"] or a["alasan"] in bk[0]["message"])) if not a["tersedia"] else \
                (w["dp"]["ok"] and not bk)
        elif k == "proforma":
            bk = [b for b in blok_pratinjau(w["proforma"]) if b.get("code") == "SO_NOT_BILLABLE"]
            ok = (bool(bk) and bk[0]["message"] == a["alasan"]) if not a["tersedia"] else (w["proforma"]["ok"] and not bk)
        elif k == "terbitfaktur":
            v = w["terbitfaktur"]
            ok = v.get("tanpa_draf", False) if not a["tersedia"] else (not v.get("tanpa_draf") and tolak(v) != SO.SI_BUKAN_DRAF)
        else:
            v = w[k]
            t = tolak(v)
            ok = (t == a["alasan"]) if not a["tersedia"] else (v["ok"] or not _keadaan(k, v))
        if a["tersedia"] and k in w and not w[k]["ok"] and k not in ("dp", "proforma", "bayar"):
            lolos_isian.append([k, w[k].get("status"), w[k].get("pesan")])
        cek(f"P {nomor} {k} tersedia={a['tersedia']}", ok, {"aksi": a, "tulis": {x: w[x] for x in w if x.startswith(k[:4])}})
    return lolos_isian


def blok_tolak(v):
    d = v.get("detail")
    return d.get("blocks", [d]) if isinstance(d, dict) else []


def pesan_kode(v):
    d = v.get("detail")
    return d.get("code") if isinstance(d, dict) else None


def _keadaan(k, v):
    """Penolakan penulis berupa blok KEADAAN aksi itu (bukan isian/lingkungan) -> tersedia=true salah."""
    kode = pesan_kode(v)
    keadaan = {"tutup": SO.KEADAAN_TUTUP, "spk": ("SPK_STATUS_TIDAK_SAH", "SPK_NONAKTIF"),
               "ubahkepala": ("SO_IS_DRAFT", "SO_EDIT_NOT_ENABLED", "SO_NOT_EDITABLE"),
               "batal": ("SO_STATUS_NOT_CANCELLABLE", "SO_HAS_SHIPMENTS_OR_INVOICES", "SO_DEPOSIT_DRAFT")}.get(k)
    if keadaan is not None:
        return kode in keadaan  # penjaga berkode: galat teks polos (mis. kalkulator non-PKP di ubah) = isian/data, ditinjau di daftar isian
    return True  # galat teks polos (konfirmasi/hapus/faktur): tersedia=true tapi ditolak = tak sah, ditinjau manusia


async def kunci_ro(conn, penilai):
    """Bungkus aksi_detail.nilai: kunci yang DIPEGANG transaksi READ ONLY di akhirnya (xact lock bertahan sampai akhir transaksi)."""
    async def intip(c):
        KUNCI.append([dict(r) for r in await c.fetch(
            "SELECT locktype, mode, COALESCE(relation::regclass::text, '') AS rel FROM pg_locks "
            "WHERE pid = pg_backend_pid() AND locktype NOT IN ('virtualxid', 'transactionid')")])
        return None
    from app.services import aksi_detail as AD
    h = await AD._nilai_asli(conn, penilai + [("__kunci", intip)])
    h.pop("__kunci")
    return h


async def main():
    if BARU:
        from app.services import aksi_detail as AD
        AD._nilai_asli = AD.nilai
        AD.nilai = kunci_ro
    pool = await ASLI[(SO, "get_pool")]()  # pool aplikasi (DB_NAME = klon)
    out = {"pohon": "baru" if BARU else "lama", "detail": {}, "tulis": {}, "waktu": [], "isian": []}
    async with pool.acquire() as conn:
        for tid in TENANT:
            sos = await conn.fetch(
                "SELECT id, order_number, status, customer_id, customer_name FROM ("
                " SELECT so.*, row_number() OVER (PARTITION BY status ORDER BY order_date DESC, order_number DESC) AS n "
                " FROM sales_orders so WHERE so.tenant_id = $1) x WHERE n <= $2 ORDER BY status, order_number", tid, SAMPEL)
            akun_kas = await conn.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, "
                                           "account_name LIMIT 1", tid)
            gudang = await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY name LIMIT 1", tid)
            print(f"[{tid}] SO sampel {len(sos)}", flush=True)
            for so in sos:
                kunci = f"{tid}:{so['order_number']}"
                d = await coba(SO.get_sales_order_detail(req(tid, "GET"), str(so["id"])))
                data = (d.get("hasil") or {}).get("data") or {}
                at = data.pop("aksi_tersedia", None)
                out["detail"][kunci] = {"ok": d["ok"], "data": data, "galat": d.get("detail")}
                w = await penulis(conn, tid, so, akun_kas, gudang)
                out["tulis"][kunci] = ringkas_tulis(w)
                if BARU:
                    cek(f"A {kunci} aksi_tersedia ada + kunci persis", d["ok"] and list(at or {}) == list(SO.AKSI_DETAIL_SO), at)
                    if d["ok"]:
                        out["isian"] += [[kunci, so["status"]] + x for x in paritas(tid, so, at, w)]
            for putaran in range(3):
                for so in sos:
                    t0 = time.perf_counter()
                    await coba(SO.get_sales_order_detail(req(tid, "GET"), str(so["id"])))
                    if putaran:  # putaran 0 = pemanasan
                        out["waktu"].append((time.perf_counter() - t0) * 1000)
    if BARU:
        buruk = [k for ks in KUNCI for k in ks if k["locktype"] == "advisory" or k["mode"] in ("RowShareLock", "RowExclusiveLock")]
        cek(f"K kunci transaksi READ ONLY aksi_tersedia ({len(KUNCI)} GET): nol advisory/RowShare/RowExclusive", KUNCI and not buruk, buruk[:5])
        out["mode_kunci"] = sorted({k["mode"] for ks in KUNCI for k in ks})
    out["cek"] = CEK
    with open(os.environ["KELUAR"], "w") as f:
        json.dump(out, f, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:40]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; isian-ditolak {len(out['isian'])}", flush=True)


asyncio.run(main())
