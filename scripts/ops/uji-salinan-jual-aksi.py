"""aksi_tersedia DETAIL jual J1b (Penawaran, Proforma, Faktur, Surat Jalan, Penerimaan, Uang Muka, Nota Kredit) -- uji di SALINAN: SATU
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
from app.routers import quotes as QT, proformas as PF, sales_invoices as SI, deliveries as DL, sj_so as SJR  # noqa: E402
from app.routers import receive_payments as RP, customer_deposits as CD, credit_notes as NK, dokumen as DK  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402
from app.schemas import quotes as SQ, customer_deposits as SCD, credit_notes as SCN, receive_payments as SRP  # noqa: E402
from app.schemas.sales_invoices import VoidInvoiceRequest  # noqa: E402
from app.services.so_pengiriman import SOFulfillRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
SAMPEL = int(os.environ.get("SAMPEL", "8"))
BARU = hasattr(SI, "aksi_tersedia_si")
MOD = (QT, PF, SI, DL, SJR, RP, CD, NK, DK, SO)
ASLI = {(m, n): getattr(m, n) for m in MOD for n in ("get_pool", "get_db_pool", "_pool") if hasattr(m, n)}
CEK, KUNCI = [], []
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
def spek(tid, kas):
    r = lambda: req(tid)  # noqa: E731
    U = lambda x: uuid.UUID(str(x))  # noqa: E731
    return {
        "penawaran": {
            "sql": "SELECT id, status, quote_number AS nomor FROM quotes WHERE tenant_id = $1",
            "detail": lambda i: QT.get_quote_detail(req(tid, "GET"), str(i)), "kunci": "AKSI_DETAIL_PN",
            "aksi": {
                "kirim": ("tulis", lambda c, row: QT.send_quote(r(), str(row["id"]), SQ.SendQuoteRequest(), Response()), None),
                "kirimdok": ("tulis", lambda c, row: DK.bagikan_dokumen(req(tid, badan={"channel": "link"}), "quotation", str(row["id"])), None),
                "terima": ("tulis", lambda c, row: QT.accept_quote(r(), str(row["id"]), Response()), None),
                "pesanan": ("tulis", lambda c, row: QT.convert_to_sales_order(r(), str(row["id"]), None), None),
                "tolak": ("tulis", lambda c, row: QT.decline_quote(r(), str(row["id"]), SQ.DeclineQuoteRequest(reason="UJI aksi"), Response()), None),
                "perpanjang": ("tulis", lambda c, row: QT.update_quote(r(), str(row["id"]), SQ.UpdateQuoteRequest(expiry_date=HARI + timedelta(days=60)),
                                                                        Response()), None),
                "batal": ("tulis", lambda c, row: QT.void_quote(r(), str(row["id"]), SQ.VoidQuoteRequest(reason="UJI aksi"), Response()), None),
                "hapus": ("tulis", lambda c, row: QT.delete_quote(r(), str(row["id"]), Response()), None),
            }},
        "proforma": {
            "sql": "SELECT id, status, proforma_number AS nomor, amount FROM proformas WHERE tenant_id = $1",
            "detail": lambda i: PF.get_proforma_detail(req(tid, "GET"), str(i)), "kunci": "AKSI_DETAIL_PF",
            "aksi": {
                "terbit": ("tulis", lambda c, row: PF.issue_proforma(r(), str(row["id"]), Response()), None),
                "ubah": ("pratinjau", lambda c, row: PF.preview_update_proforma(r(), str(row["id"]), PF.UpdateProformaRequest()), ("PROFORMA_NOT_DRAFT",)),
                "batal": ("tulis", lambda c, row: PF.cancel_proforma(r(), str(row["id"]), PF.CancelProformaRequest(reason="UJI aksi"), Response()), None),
                "koreksi": ("pratinjau", lambda c, row: PF.preview_correct_proforma_amount(
                    r(), str(row["id"]), PF.KoreksiNominalRequest(amount=row["amount"] or 0, reason="UJI aksi")), ("PROFORMA_NOT_ISSUED",)),
            }},
        "faktur": {
            "sql": "SELECT id, status, invoice_number AS nomor, sales_order_id, total_amount FROM sales_invoices WHERE tenant_id = $1",
            "detail": lambda i: SI.get_invoice(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_SI",
            "aksi": {
                "terbit": ("tulis", lambda c, row: SI.post_invoice(r(), row["id"], None, Response()), None),
                "hapusdraf": ("tulis", lambda c, row: SI.delete_invoice(r(), row["id"], Response()), None),
                "bayar": ("pratinjau", lambda c, row: SI.preview_receive_payment_for_invoice(r(), row["id"], None),
                          ("RP_INVOICE_DRAFT", "RP_INVOICE_VOID", "RP_NOTHING_DUE", "RP_CUSTOMER_MISSING")),
                "kirim": ("kirim", None, None),
                "nk": ("pratinjau", lambda c, row: NK.preview_credit_note(r(), NK.CreditNotePreviewRequest(
                    original_invoice_id=str(row["id"]), reason="return")), "PESAN:Faktur belum dibukukan atau sudah dibatalkan."),
                "void": ("tulis", lambda c, row: SI.void_invoice(r(), row["id"], VoidInvoiceRequest(reason="UJI aksi"), Response()), None),
            }},
        "surat_jalan": {
            "sql": "SELECT id, status, fulfillment_number AS nomor FROM invoice_fulfillments WHERE tenant_id = $1",
            "detail": lambda i: DL.get_delivery_detail(str(i), req(tid, "GET")), "kunci": "AKSI_DETAIL_SJ",
            "aksi": {"sjbatal": ("tulis", lambda c, row: SJR.void_sj(r(), str(row["id"]), SJR.AlasanIn(reason="UJI aksi"), Response()), None)}},
        "penerimaan": {
            "sql": "SELECT id, status, payment_number AS nomor FROM receive_payments WHERE tenant_id = $1",
            "detail": lambda i: RP.get_receive_payment(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_RP",
            "aksi": {
                "rpposting": ("tulis", lambda c, row: RP.post_receive_payment(r(), row["id"]), None),
                "rphapus": ("tulis", lambda c, row: RP.delete_receive_payment(r(), row["id"]), None),
                "batal": ("tulis", lambda c, row: RP.void_receive_payment(r(), row["id"], SRP.VoidPaymentRequest(void_reason="UJI aksi"), Response()),
                          None),
            }},
        "uang_muka": {
            "sql": "SELECT id, status, deposit_number AS nomor FROM customer_deposits WHERE tenant_id = $1",
            "detail": lambda i: CD.get_customer_deposit(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_DP",
            "aksi": {
                "terapkan": ("pratinjau", lambda c, row: CD.preview_apply_customer_deposit(r(), row["id"], SCD.ApplyCustomerDepositRequest(
                    applications=[{"invoice_id": str(uuid.uuid4()), "amount": 1}])), ("DEPOSIT_NOT_APPLYABLE",)),
                "lepas": ("lepas", None, None),
                "refund": ("pratinjau", lambda c, row: CD.preview_refund_customer_deposit(r(), row["id"], SCD.RefundCustomerDepositRequest(
                    amount=1, refund_date=HARI, payment_method="cash", account_id=str(kas))), ("DEPOSIT_NOT_REFUNDABLE",)),
                "batal": ("tulis", lambda c, row: CD.void_customer_deposit(r(), row["id"], SCD.VoidCustomerDepositRequest(reason="UJI aksi"),
                                                                            Response()), None),
                "pindah": ("pratinjau", lambda c, row: CD.preview_move_customer_deposit_proforma(r(), row["id"], CD.PindahProformaRequest(
                    target_proforma_id=str(uuid.uuid4()))), ("DP_TIDAK_BISA_DIPINDAH", "DP_TANPA_PESANAN")),
            }},
        "nota_kredit": {
            "sql": "SELECT id, status, credit_note_number AS nomor, total_amount FROM credit_notes WHERE tenant_id = $1",
            "detail": lambda i: NK.get_credit_note(req(tid, "GET"), U(i)), "kunci": "AKSI_DETAIL_NK",
            "aksi": {
                "terbit": ("tulis", lambda c, row: NK.post_credit_note(r(), row["id"], Response()), None),
                "batal": ("tulis", lambda c, row: NK.void_credit_note(r(), row["id"], SCN.VoidCreditNoteRequest(reason="UJI aksi"), Response()), None),
                "terapkan": ("pratinjau", lambda c, row: NK.preview_apply_credit_note(r(), row["id"], SCN.ApplyCreditNoteRequest(
                    applications=[{"invoice_id": str(uuid.uuid4()), "amount": row["total_amount"] or 1}])),
                    ("CN_SUDAH_TERKAIT", "CN_BELUM_TERBIT", "CN_SUDAH_DIKEMBALIKAN")),
                "lepas": ("tulis", lambda c, row: NK.unapply_credit_note(r(), row["id"], SCN.UnapplyCreditNoteRequest(reason="UJI aksi"), Response()),
                          None),
                "refund": ("pratinjau", lambda c, row: NK.preview_refund_credit_note(r(), row["id"], SCN.RefundCreditNoteRequest(
                    amount=1, refund_date=HARI, payment_method="cash", account_id=str(kas))), ("CN_TAK_BISA_REFUND",)),
            }},
    }


async def kirim_faktur(conn, tid, row):
    """kirim detail faktur: ber-SO -> dua penulis SO (fulfill + SJ tanpa faktur); tanpa SO -> POST /sales-invoices/{id}/fulfill baris 1."""
    if row["sales_order_id"]:
        so = str(row["sales_order_id"])
        return {"so": True, "kirim": await tulis(conn, lambda: SO.fulfill_order(req(tid), Response(), so, SOFulfillRequest())),
                "kirim_sj": await tulis(conn, lambda: SJR.create_sj_so(req(tid), Response(), so, None))}
    item = await conn.fetchval("SELECT sii.id FROM sales_invoice_items sii JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = $1 "
                               "WHERE sii.invoice_id = $2 ORDER BY sii.line_number LIMIT 1", tid, row["id"])
    badan = {"items": [{"invoice_item_id": str(item), "quantity": 1}] if item else []}
    return {"so": False, "kirim": await tulis(conn, lambda: SI.fulfill_invoice(req(tid, badan=badan), row["id"]))}


async def lepas_dp(conn, tid, row):
    apps = await conn.fetch("SELECT id FROM customer_deposit_applications WHERE deposit_id = $1 AND tenant_id = $2 "
                            "AND COALESCE(status, 'active') <> 'reversed' AND reversed_by_id IS NULL ORDER BY id", row["id"], tid)
    return {"apps": [await coba(CD.preview_reverse_customer_deposit_application(req(tid), row["id"], a["id"])) for a in apps]}


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


def paritas(modul, nomor, kunci, a, mode, keadaan, v, isian):
    nama = f"P {modul} {nomor} {kunci} tersedia={a['tersedia']}"
    if isinstance(v, dict) and v.get("status") == 403 and "izin" in (v.get("pesan") or ""):
        BELUM_TERUKUR.append([modul, nomor, kunci, a["tersedia"], v.get("pesan")])
        return
    if mode == "tulis":
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
    elif mode == "kirim":
        if v["so"]:
            f_kd = [b for b in blok(v["kirim"]) if b.get("code") in SO.KEADAAN_KIRIM] if not v["kirim"]["ok"] else []
            d = v["kirim_sj"].get("detail")
            sj_tutup = not v["kirim_sj"]["ok"] and isinstance(d, dict) and d.get("code") in SO.KEADAAN_KIRIM_SJ + ("SJ_SO_NONAKTIF",)
            ok = (bool(f_kd) and sj_tutup and a["alasan"] in [f_kd[0].get("message"), v["kirim_sj"].get("pesan")]) if not a["tersedia"] \
                else not (f_kd and sj_tutup)
        else:
            w = v["kirim"]
            ok = (not w["ok"] and w.get("pesan") == a["alasan"]) if not a["tersedia"] else True
            if a["tersedia"] and not w["ok"]:
                isian.append([modul, nomor, kunci, w.get("status"), w.get("pesan")])
    elif mode == "lepas":
        bebas = [x for x in v["apps"] if x["ok"] and not blok(x)]
        if not a["tersedia"]:
            ok = not bebas and (a["alasan"] == CD.PENERAPAN_DP_TAK_ADA if not v["apps"] else
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
    SI.hapus_objek_sesudah_commit = hapus_tiruan
    for kelas in (getattr(SS, n) for n in dir(SS)):
        if isinstance(kelas, type) and hasattr(kelas, "delete_file"):
            kelas.delete_file = del_tiruan


async def main():
    lepas_penyimpanan()
    if BARU:
        from app.services import aksi_detail as AD
        AD._nilai_asli = AD.nilai
        AD.nilai = kunci_ro
    pool = await ASLI[(SI, "get_pool")]()
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
                        if mode == "kirim":
                            hasil[kunci] = await kirim_faktur(conn, tid, row)
                        elif mode == "lepas":
                            hasil[kunci] = await lepas_dp(conn, tid, row)
                        elif mode == "tulis":
                            hasil[kunci] = await tulis(conn, lambda: f(conn, row))
                        else:
                            hasil[kunci] = await coba(f(conn, row))
                    out["tulis"][f"{modul}:{nomor}"] = {k: ringkas(v) for k, v in hasil.items()}
                    if BARU:
                        harap = list(getattr({"AKSI_DETAIL_PN": QT, "AKSI_DETAIL_PF": PF, "AKSI_DETAIL_SI": SI, "AKSI_DETAIL_SJ": DL,
                                              "AKSI_DETAIL_RP": RP, "AKSI_DETAIL_DP": CD, "AKSI_DETAIL_NK": NK}[s["kunci"]], s["kunci"]))
                        cek(f"A {modul} {nomor} aksi_tersedia ada + kunci persis", d["ok"] and list(at or {}) == harap, at or d.get("detail"))
                        if d["ok"] and at:
                            for kunci, (mode, f, keadaan) in s["aksi"].items():
                                paritas(modul, nomor, kunci, at[kunci], mode, keadaan, hasil[kunci], out["isian"])
                if BARU:
                    m_aksi = {"AKSI_DETAIL_PN": (QT, "aksi_tersedia_pn"), "AKSI_DETAIL_PF": (PF, "aksi_tersedia_pf"),
                              "AKSI_DETAIL_SI": (SI, "aksi_tersedia_si"), "AKSI_DETAIL_SJ": (DL, "aksi_tersedia_sj"),
                              "AKSI_DETAIL_RP": (RP, "aksi_tersedia_rp"), "AKSI_DETAIL_DP": (CD, "aksi_tersedia_dp"),
                              "AKSI_DETAIL_NK": (NK, "aksi_tersedia_nk")}[s["kunci"]]
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
    out["waktu_kunci"] = WAKTU_KUNCI
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:60]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[info] objek tiruan 'dihapus': {len(BERKAS_TIRUAN)} (MinIO produksi tak disentuh)", flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus; isian-ditolak {len(out['isian'])}", flush=True)


asyncio.run(main())
