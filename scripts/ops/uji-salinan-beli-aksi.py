"""aksi_tersedia DETAIL kelompok BELI (PO, Penerimaan Barang, Faktur Pembelian, Pembayaran Keluar, Nota Debit, UM Pembelian) -- uji di SALINAN: SATU
dump, DUA klon, handler ASLI pohon LAMA (master) dan BARU. Penulis dijalankan di transaksi LUAR yang di-ROLLBACK (klon utuh antar-langkah).
 A (dua pohon) GET detail sampel (per tenant x modul x status <= SAMPEL): baru = lama + aksi_tersedia.
 W (dua pohon) tiap kunci: PENULIS nyata (rollback) untuk aksi tanpa isian nominal/tujuan, PRATINJAU (penentu sama) untuk aksi ber-isian;
   hasil lama == baru -> perilaku TULIS identik (penjaga yang diekstrak, saklar kunci void penawaran).
 P (baru) paritas: tersedia=false => penulis/pratinjau menolak dgn kalimat == alasan; tersedia=true => tak ditolak blok KEADAAN.
   Penolakan lain pada tersedia=true (isian/data/lingkungan) dicatat untuk DITINJAU, bukan dibuang.
 K (baru) kunci yang DIPEGANG transaksi READ ONLY di akhirnya (pg_locks pid sendiri): nol advisory/RowShare/RowExclusive.
 T (dua pohon) waktu GET detail per modul (3 putaran, p50/p95) -> banding."""
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as PO, goods_receipts as GR, bills as BL, bill_payments as BP  # noqa: E402
from app.routers import vendor_credits as VC, vendor_deposits as VD  # noqa: E402
from app.schemas import purchase_orders as SPO, bills as SBL, bill_payments as SBP, vendor_credits as SVC, vendor_deposits as SVD  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
SAMPEL = int(os.environ.get("SAMPEL", "8"))
BARU = hasattr(BL, "aksi_tersedia_fpb")
MOD = (PO, GR, BL, BP, VC, VD)
ASLI = {(m, n): getattr(m, n) for m in MOD for n in ("get_pool", "get_db_pool", "_pool") if hasattr(m, n)}
CEK, KUNCI = [], []
MODMAP = {"AKSI_DETAIL_PO": (PO, "aksi_tersedia_po"), "AKSI_DETAIL_GRN": (GR, "aksi_tersedia_grn"), "AKSI_DETAIL_FPB": (BL, "aksi_tersedia_fpb"),
          "AKSI_DETAIL_PK": (BP, "aksi_tersedia_pk"), "AKSI_DETAIL_ND": (VC, "aksi_tersedia_nd"), "AKSI_DETAIL_UMP": (VD, "aksi_tersedia_ump")}
HARI = date.today()


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:700]])


def req(tid, method="POST", badan=None):
    isi = json.dumps(badan).encode() if badan is not None else b""

    async def terima():
        return {"type": "http.request", "body": isi, "more_body": False}
    r = Request({"type": "http", "method": method, "path": "/", "headers": [(b"content-type", b"application/json")], "query_string": b""},
                terima)
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-jual-aksi",
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
        return {"ok": False, "status": e.status_code, "detail": j(e.detail), "pesan": pesan(e.detail), "code": getattr(e, "code", None)}
    except Exception as e:  # noqa: BLE001 -- 500 dicatat
        return {"ok": False, "status": 500, "detail": repr(e)[:300], "pesan": repr(e)[:300]}


class Kolam:
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
    tr = conn.transaction()
    await tr.start()
    pasang(Kolam(conn))
    try:
        return await coba(f())
    finally:
        lepas()
        await tr.rollback()


def blok(v):
    """Blok pratinjau (data.blocks) atau blok penolakan penulis (detail.blocks / detail)."""
    if v["ok"]:
        d = (v.get("hasil") or {}).get("data") or {}
        return d.get("blocks") or [] if isinstance(d, dict) else []
    d = v.get("detail")
    return (d.get("blocks") or [d]) if isinstance(d, dict) else [{"message": d}]


# ---------------------------------------------------------------- spesifikasi per modul
KD_TAGIH = ("PO_TAK_BISA_DITAGIH", "TAK_ADA_SISA_TAGIH")


def spek(tid, kas):
    r = lambda: req(tid)  # noqa: E731
    U = lambda x: uuid.UUID(str(x))  # noqa: E731

    async def poum(c, row):
        if not row["vendor_id"]:
            raise HTTPException(422, "PO tanpa vendor: badan UM tak bisa dibentuk")
        return await VD.preview_create_vendor_deposit(r(), SVD.VendorDepositCreate(
            deposit_date=HARI, vendor_id=row["vendor_id"], purchase_order_id=row["id"], amount=1))
    return {
        "po": {
            "sql": "SELECT id, status, po_number AS nomor, vendor_id FROM purchase_orders WHERE tenant_id = $1",
            "detail": lambda i: PO.get_purchase_order(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_PO",
            "aksi": {
                "pokirim": ("tulis", lambda c, row: PO.send_purchase_order(r(), row["id"], Response()), None),
                "pohapus": ("tulis", lambda c, row: PO.delete_purchase_order(r(), row["id"], Response()), None),
                "poterima": ("pratinjau", lambda c, row: PO.preview_receive_goods(r(), row["id"], None),
                             ("PO_TAK_BISA_DITERIMA", "PO_TANPA_BARANG", "TAK_ADA_SISA_TERIMA")),
                "potagih": ("pratinjau", lambda c, row: PO.preview_convert_to_bill(r(), row["id"], None), KD_TAGIH),
                "potutup": ("tulis", lambda c, row: PO.close_purchase_order(r(), row["id"], Response()), None),
                "pobatal": ("tulis", lambda c, row: PO.cancel_purchase_order(r(), row["id"], SPO.CancelPurchaseOrderRequest(reason="UJI aksi"),
                                                                             Response()), None),
                "poum": ("pratinjau", poum, ("VENDOR_TIDAK_DITEMUKAN", "PO_TIDAK_DITEMUKAN", "PO_BEDA_VENDOR", "PO_DIBATALKAN",
                                            "UMP_MELEBIHI_SISA_PO")),
            }},
        "grn": {
            "sql": "SELECT id, status, receipt_number AS nomor, purchase_order_id FROM goods_receipts WHERE tenant_id = $1",
            "detail": lambda i: GR.get_goods_receipt(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_GRN",
            "aksi": {
                "grntagih": ("pratinjau", lambda c, row: PO.preview_convert_to_bill(r(), row["purchase_order_id"], None), KD_TAGIH),
                "grnbatal": ("tulis", lambda c, row: GR.void_goods_receipt(r(), row["id"], GR.VoidGoodsReceiptRequest(reason="UJI aksi"),
                                                                           Response()), None),
            }},
        "faktur_beli": {
            "sql": "SELECT id, COALESCE(status_v2, status) AS status, invoice_number AS nomor FROM bills WHERE tenant_id = $1",
            "detail": lambda i: BL.get_bill_v2(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_FPB",
            "aksi": {
                "fpbayar": ("fpbayar", lambda c, row: BL.preview_record_payment(r(), row["id"], SBL.RecordPaymentRequest(
                    amount=1, payment_method="cash", account_id=kas)), ("TAGIHAN_TIDAK_DITEMUKAN", "TAGIHAN_DIBATALKAN", "TAGIHAN_DRAF")),
                "fpposting": ("tulis", lambda c, row: BL.post_bill(r(), row["id"], Response(), []), None),
                "fphapus": ("tulis", lambda c, row: BL.delete_bill(r(), row["id"], Response()), None),
                "fpbatal": ("tulis", lambda c, row: BL.void_bill(r(), row["id"], SBL.VoidBillRequest(reason="UJI aksi"), Response()), None),
                "fpnd": ("fpnd", lambda c, row: BL.get_bill_detail(req(tid, "GET"), row["id"]), None),
            }},
        "bayar_keluar": {
            "sql": "SELECT id, status, payment_number AS nomor FROM bill_payments_v2 WHERE tenant_id = $1",
            "detail": lambda i: BP.get_bill_payment(req(tid, "GET"), str(i)), "kunci": "AKSI_DETAIL_PK",
            "aksi": {
                "pkposting": ("tulis", lambda c, row: BP.post_bill_payment(r(), str(row["id"]), Response()), None),
                "pkhapus": ("tulis", lambda c, row: BP.delete_bill_payment(r(), str(row["id"]), Response()), None),
                "pkbatal": ("tulis", lambda c, row: BP.void_bill_payment(r(), str(row["id"]), SBP.VoidBillPaymentRequest(void_reason="UJI aksi"),
                                                                         Response()), None),
            }},
        "nota_debit": {
            "sql": "SELECT id, status, credit_number AS nomor FROM vendor_credits WHERE tenant_id = $1",
            "detail": lambda i: VC.get_vendor_credit(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_ND",
            "aksi": {
                "ndposting": ("tulis", lambda c, row: VC.post_vendor_credit(r(), row["id"], Response()), None),
                "ndterapkan": ("pratinjau", lambda c, row: VC.preview_apply_vendor_credit(r(), row["id"], SVC.ApplyVendorCreditRequest(
                    applications=[{"bill_id": str(uuid.uuid4()), "amount": 1}])),
                    ("TIDAK_DITEMUKAN", "ND_SUDAH_BERTAUT", "BELUM_DIBUKUKAN", "SUDAH_DIKEMBALIKAN")),
                "ndkembali": ("pratinjau", lambda c, row: VC.preview_receive_refund_vendor_credit(r(), row["id"], SVC.ReceiveRefundRequest(
                    amount=1, refund_date=HARI, payment_method="cash", account_id=str(kas))), ("TIDAK_DITEMUKAN", "STATUS_TAK_SAH", "ND_BERTAUT")),
                "ndlepas": ("tulis", lambda c, row: VC.unapply_vendor_credit(r(), row["id"], SVC.UnapplyVendorCreditRequest(reason="UJI aksi"),
                                                                             Response()), None),
                "ndbatal": ("tulis", lambda c, row: VC.void_vendor_credit(r(), row["id"], SVC.VoidVendorCreditRequest(reason="UJI aksi"),
                                                                          Response()), None),
            }},
        "um_beli": {
            "sql": "SELECT id, status, deposit_number AS nomor FROM vendor_deposits WHERE tenant_id = $1",
            "detail": lambda i: VD.get_vendor_deposit(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_UMP",
            "aksi": {
                "umposting": ("tulis", lambda c, row: VD.post_vendor_deposit(r(), row["id"], Response()), None),
                "umhapus": ("tulis", lambda c, row: VD.delete_vendor_deposit(r(), row["id"], Response()), None),
                "umterapkan": ("pratinjau", lambda c, row: VD.preview_apply_vendor_deposit(r(), row["id"], SVD.ApplyDepositRequest(
                    bill_id=uuid.uuid4(), amount=1)), ("BELUM_DIPOSTING", "SALDO_TIDAK_CUKUP")),
                "umlepas": ("lepas", None, "Penerapan uang muka tidak ditemukan."),
                "umkembalikan": ("pratinjau", lambda c, row: VD.preview_refund_vendor_deposit(r(), row["id"], SVD.VendorDepositRefundCreate(
                    refund_date=HARI, amount=1, account_id=kas)), ("BELUM_DIPOSTING", "SALDO_TIDAK_CUKUP")),
                "umbatal": ("tulis", lambda c, row: VD.void_vendor_deposit(r(), row["id"], SVD.VoidDepositRequest(reason="UJI aksi"), Response()),
                            None),
            }},
    }


async def lepas_ump(conn, tid, row):
    apps = await conn.fetch("SELECT a.id FROM vendor_deposit_applications a JOIN vendor_deposits d ON d.id = a.vendor_deposit_id "
                            "AND d.tenant_id = $2 WHERE a.vendor_deposit_id = $1 AND a.status = 'active' ORDER BY a.id", row["id"], tid)
    return {"apps": [await coba(VD.preview_unapply_vendor_deposit(req(tid), row["id"], a["id"])) for a in apps]}


def ringkas(v):
    if isinstance(v, dict) and "ok" in v:
        return {"ok": v["ok"], "status": v.get("status"), "detail": v.get("detail"),
                "blocks": [[b.get("code"), b.get("message")] for b in blok(v)] if v["ok"] else None}
    if isinstance(v, dict) and "apps" in v:
        return {"apps": [ringkas(x) for x in v["apps"]]}
    if isinstance(v, dict) and "so" in v:
        return {k: ringkas(x) if isinstance(x, dict) else x for k, x in v.items()}
    return v


BELUM_TERUKUR = []
ULANG = []
ULANG_DIBLOK_PRATINJAU = ("Pembayaran sudah dibatalkan.", "Uang muka ini sudah dibatalkan.")


def paritas(modul, nomor, kunci, a, mode, keadaan, v, isian):
    nama = f"P {modul} {nomor} {kunci} tersedia={a['tersedia']}"
    if isinstance(v, dict) and v.get("status") == 403 and "izin" in (v.get("pesan") or ""):
        BELUM_TERUKUR.append([modul, nomor, kunci, a["tersedia"], v.get("pesan")])
        return
    if mode == "tulis":
        if not a["tersedia"] and v["ok"] and a["alasan"] in ULANG_DIBLOK_PRATINJAU:
            ULANG.append([modul, nomor, kunci, a["alasan"]])  # penentu pratinjau memblok; penulis = ulang idempoten 200 (docstring penentu)
            return
        if not a["tersedia"]:
            ok = not v["ok"] and (v.get("pesan") == a["alasan"] or a["alasan"] in [b.get("message") for b in blok(v)])
        else:
            ok = True
            if not v["ok"]:
                isian.append([modul, nomor, kunci, v.get("status"), v.get("pesan")])
    elif mode == "pratinjau":
        bk = blok(v)
        if isinstance(keadaan, str) and keadaan.startswith("PESAN:"):
            kd = [b for b in bk if b.get("message") == keadaan[6:]]
        else:
            kd = [b for b in bk if b.get("code") in keadaan]
        if not a["tersedia"]:
            ok = any(b.get("message") == a["alasan"] or a["alasan"] in (b.get("message") or "") for b in bk) or \
                (not v["ok"] and v.get("pesan") == a["alasan"])
        else:
            ok = not kd
            if not v["ok"]:
                isian.append([modul, nomor, kunci, v.get("status"), v.get("pesan")])
    elif mode == "fpbayar":
        bk = blok(v)
        from app.services import teks_galat as tg
        if not a["tersedia"] and a["alasan"].endswith("sudah lunas."):  # sisa tagihan 0: pratinjau nominal 1 -> MELEBIHI_SISA_TAGIHAN sisa Rp0
            ok = any(b.get("code") == "MELEBIHI_SISA_TAGIHAN" and f"sisa tagihan {tg.rp(0)}" in (b.get("message") or "") for b in bk)
        elif not a["tersedia"]:
            ok = any(b.get("message") == a["alasan"] for b in bk) or (not v["ok"] and v.get("pesan") == a["alasan"])
        else:
            ok = not [b for b in bk if b.get("code") in keadaan or b.get("code") == "MELEBIHI_SISA_TAGIHAN"]
            if not v["ok"]:
                isian.append([modul, nomor, kunci, v.get("status"), v.get("pesan")])
    elif mode == "fpnd":
        top = (((v.get("hasil") or {}).get("aksi_tersedia")) or {}).get("nota_debit")
        ok = v["ok"] and top == a  # penentu SAMA dgn aksi_tersedia.nota_debit level atas (petunjuk lama)
    elif mode == "lepas":
        bebas = [x for x in v["apps"] if x["ok"] and not blok(x)]
        if not a["tersedia"]:
            ok = not bebas and (a["alasan"] == keadaan if not v["apps"] else
                                any(a["alasan"] in [b.get("message") for b in blok(x)] for x in v["apps"]))
        else:
            ok = bool(bebas)
    else:
        ok = False
    cek(nama, ok, {"aksi": a, "hasil": ringkas(v)})


MODUL_KINI = [None]
WAKTU_KUNCI = {}


def berwaktu(kunci, fn):
    async def f(c):
        t0 = time.perf_counter()
        try:
            return await fn(c)
        finally:
            WAKTU_KUNCI.setdefault(f"{MODUL_KINI[0]}.{kunci}", []).append((time.perf_counter() - t0) * 1000)
    return f


async def kunci_ro(conn, penilai):
    async def intip(c):
        KUNCI.append([dict(r) for r in await c.fetch(
            "SELECT locktype, mode, COALESCE(relation::regclass::text, '') AS rel FROM pg_locks "
            "WHERE pid = pg_backend_pid() AND locktype NOT IN ('virtualxid', 'transactionid')")])
        return None
    from app.services import aksi_detail as AD
    h = await AD._nilai_asli(conn, [(k, berwaktu(k, f)) for k, f in penilai] + [("__kunci", intip)])
    h.pop("__kunci")
    return h


BERKAS_TIRUAN = []


def lepas_penyimpanan():
    """Penyimpanan objek TIRUAN: hapus draf faktur menghapus objek lampiran SESUDAH commit -> JANGAN sentuh MinIO produksi."""
    from app.services import lampiran_milik as LM
    from app.services import storage_service as SS

    async def hapus_tiruan(paths, konteks):
        BERKAS_TIRUAN.extend(paths)
        return {"dihapus": 0, "gagal": []}

    async def del_tiruan(self, path):
        BERKAS_TIRUAN.append(path)
        return True
    LM.hapus_objek_sesudah_commit = hapus_tiruan
    for m in MOD:
        if hasattr(m, "hapus_objek_sesudah_commit"):
            m.hapus_objek_sesudah_commit = hapus_tiruan
    for kelas in (getattr(SS, n) for n in dir(SS)):
        if isinstance(kelas, type) and hasattr(kelas, "delete_file"):
            kelas.delete_file = del_tiruan


async def main():
    lepas_penyimpanan()
    if BARU:
        from app.services import aksi_detail as AD
        AD._nilai_asli = AD.nilai
        AD.nilai = kunci_ro
    pool = await next(f for (m, n), f in ASLI.items() if n == "get_pool")()
    out = {"pohon": "baru" if BARU else "lama", "detail": {}, "tulis": {}, "waktu": {}, "isian": []}
    async with pool.acquire() as conn:
        for tid in TENANT:
            kas = await conn.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, "
                                      "account_name LIMIT 1", tid)
            for modul, s in spek(tid, kas).items():
                rows = await conn.fetch(
                    f"SELECT * FROM (SELECT x.*, row_number() OVER (PARTITION BY status ORDER BY nomor DESC) AS n FROM ({s['sql']}) x) y "
                    "WHERE n <= $2 ORDER BY status, nomor", tid, SAMPEL)
                print(f"[{tid}] {modul} sampel {len(rows)}", flush=True)
                MODUL_KINI[0] = modul
                for row in rows:
                    nomor = f"{tid}:{row['nomor']}:{row['status']}"
                    d = await coba(s["detail"](row["id"]))
                    h = d.get("hasil") or {}
                    data = dict(h.get("data") or h) if isinstance(h, dict) else {}
                    at = data.pop("aksi_tersedia", None)
                    out["detail"][f"{modul}:{nomor}"] = {"ok": d["ok"], "data": data, "galat": d.get("detail")}
                    hasil = {}
                    for kunci, (mode, f, keadaan) in s["aksi"].items():
                        if mode == "lepas":
                            hasil[kunci] = await lepas_ump(conn, tid, row)
                        elif mode == "tulis":
                            hasil[kunci] = await tulis(conn, lambda: f(conn, row))
                        else:
                            hasil[kunci] = await coba(f(conn, row))
                    out["tulis"][f"{modul}:{nomor}"] = {k: ringkas(v) for k, v in hasil.items()}
                    if BARU:
                        harap = list(getattr(MODMAP[s["kunci"]][0], s["kunci"]))
                        cek(f"A {modul} {nomor} aksi_tersedia ada + kunci persis", d["ok"] and list(at or {}) == harap, at or d.get("detail"))
                        if d["ok"] and at:
                            for kunci, (mode, f, keadaan) in s["aksi"].items():
                                paritas(modul, nomor, kunci, at[kunci], mode, keadaan, hasil[kunci], out["isian"])
                if BARU:
                    m_aksi = MODMAP[s["kunci"]]
                    asli_fn = getattr(*m_aksi)

                    async def mati(*a, **k):
                        return {}
                    ww = out.setdefault("waktu_onoff", {}).setdefault(modul, {"off": [], "on": []})
                    for putaran in range(6):
                        for row in rows:
                            for keadaan_ in (("off", mati), ("on", asli_fn)):
                                setattr(m_aksi[0], m_aksi[1], keadaan_[1])
                                t0 = time.perf_counter()
                                await coba(s["detail"](row["id"]))
                                if putaran:
                                    ww[keadaan_[0]].append((time.perf_counter() - t0) * 1000)
                    setattr(m_aksi[0], m_aksi[1], asli_fn)
                w = out["waktu"].setdefault(modul, [])
                for putaran in range(3):
                    for row in rows:
                        t0 = time.perf_counter()
                        await coba(s["detail"](row["id"]))
                        if putaran:
                            w.append((time.perf_counter() - t0) * 1000)
    if BARU:
        buruk = [k for ks in KUNCI for k in ks if k["locktype"] == "advisory" or k["mode"] in ("RowShareLock", "RowExclusiveLock")]
        cek(f"K kunci transaksi READ ONLY aksi_tersedia ({len(KUNCI)} GET): nol advisory/RowShare/RowExclusive", KUNCI and not buruk, buruk[:5])
        out["mode_kunci"] = sorted({k["mode"] for ks in KUNCI for k in ks})
    out["cek"] = CEK
    out["belum_terukur"] = BELUM_TERUKUR
    out["ulang_idempoten"] = ULANG
    out["waktu_kunci"] = WAKTU_KUNCI
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:60]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[info] objek tiruan 'dihapus': {len(BERKAS_TIRUAN)} (MinIO produksi tak disentuh)", flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; isian-ditolak {len(out['isian'])}", flush=True)


asyncio.run(main())
