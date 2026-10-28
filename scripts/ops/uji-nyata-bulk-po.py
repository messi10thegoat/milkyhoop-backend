"""Harness NYATA B1b-1 (TULIS kaos, penanda 'TES E2E'; aturan tetap pemilik 30 Sep): kirim / batal / hapus massal PO lewat handler ASLI
terhadap DB produksi tenant kaos-biru-konveksi. Bukan grapgrap.

Alur: buat 4 PO draf A,B,C,D (handler create) -> D dikirim lewat rute TUNGGAL -> pratinjau kirim massal [A, D, X] -> kirim massal [A, D]
-> ulang kunci sama (replay) -> pratinjau batal tanpa/dengan alasan -> batal massal [A] -> batal ulang [A] -> pratinjau hapus [B, C, A] ->
hapus massal [B, C, A] -> bersih: D dibatalkan lewat rute tunggal. Sisa di kaos: A dan D berstatus Dibatalkan (catatan TES E2E).
Diperiksa di DB: status/sent_by/cancelled_reason, pratinjau nol tulis, replay tanpa tulis, audit BULK_*, DOCUMENT_DELETED ber-userId (V230).
Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

from app.routers import bulk_po as BP  # noqa: E402
from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as PS  # noqa: E402

# Pagar kirim-keluar (syarat MASTER): SEMUA kanal keluar (HTTP httpx/requests/aiohttp, SMTP) di-stub + dicatat; WAJIB 0 panggilan.
# Kirim PO = tandai status saja (tanpa surel/WA dari server); pagar ini membuktikannya, bukan mengandalkannya.
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
    try:
        import httpx
        httpx.Client.send = catat("httpx")
        httpx.AsyncClient.send = acatat
    except ImportError:
        pass
    try:
        import requests
        requests.Session.send = catat("requests")
    except ImportError:
        pass
    try:
        import aiohttp
        aiohttp.ClientSession._request = acatat
    except ImportError:
        pass


_pasang_pagar_keluar()

T = "kaos-biru-konveksi"
U = uuid.uuid4()  # pengguna harness (sent_by/cancelled_by tanpa FK); jejak audit = id ini
MULAI = datetime.now(timezone.utc)
PENANDA = f"TES E2E B1b-1 {MULAI.isoformat(timespec='seconds')}"


class Req:
    def __init__(self, kunci=None):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(U), "role": "OWNER"}})()
        self.query_params = {}


def ep(path):
    return next(r.endpoint for r in BP.router.routes if r.path == path)


async def main(keluar):
    pool = await asyncpg.create_pool(os.environ["URL"], min_size=1, max_size=4)

    async def gp(): return pool
    PO.get_pool = gp
    BP.get_pool = gp
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def baris(pid):
        async with pool.acquire() as c:
            return await c.fetchrow("SELECT status, sent_by, sent_at, cancelled_by, cancelled_reason, updated_at FROM purchase_orders "
                                    "WHERE id = $1 AND tenant_id = $2", pid, T)

    async with pool.acquire() as c:
        v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", T)
        hari = await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Jakarta')::date")
    ids = {}
    for nama in "ABCD":
        body = PS.CreatePurchaseOrderRequest(vendor_id=str(v["id"]), vendor_name=v["name"], po_date=hari, notes=f"{PENANDA} {nama}",
                                             items=[PS.PurchaseOrderItemCreate(description=f"Kain uji {nama}", quantity=Decimal("2"),
                                                                               unit_price=Decimal("1000"))])
        r = await PO.create_purchase_order(Req(), body)
        ids[nama] = uuid.UUID(str(r["data"]["id"]))
    catat["po"] = {k: str(x) for k, x in ids.items()}
    A, B, C, Dd = ids["A"], ids["B"], ids["C"], ids["D"]
    X = uuid.uuid4()
    k = lambda s: f"b1b1-{s}-{MULAI.timestamp():.0f}"  # noqa: E731

    # D lewat rute TUNGGAL (pembanding)
    r = await PO.send_purchase_order(Req(k("tunggal-kirim")), Dd)
    harus("tunggal kirim D", r["data"]["status"] == "sent", r)

    # pratinjau kirim massal: nol tulis
    sebelum = await baris(A)
    p = (await ep("/bulk/send/preview")(Req(), BP.BulkAksiRequest(ids=[str(A), str(Dd), str(X)])))["data"]
    kode = [(i["ok"], [b["code"] for b in i["blocks"]]) for i in p["items"]]
    harus("pratinjau kirim [A,D,X]", kode == [(True, []), (False, ["PO_TAK_BISA_DIKIRIM"]), (False, ["PO_TAK_ADA"])], kode)
    harus("pratinjau kirim nol tulis", await baris(A) == sebelum, dict(await baris(A)))

    # kirim massal + replay
    w = (await ep("/bulk/send")(Req(k("kirim")), BP.BulkAksiRequest(ids=[str(A), str(Dd)])))["data"]
    st = [(i["status"], i["http"], i["code"]) for i in w["items"]]
    harus("kirim massal [A,D]", st == [("ok", 200, None), ("rejected", 400, "PO_TAK_BISA_DIKIRIM")], st)
    a1, d1 = await baris(A), await baris(Dd)
    harus("A terkirim = D tunggal (status, sent_by)", (a1["status"], a1["sent_by"]) == ("sent", U) == (d1["status"], d1["sent_by"]),
          (dict(a1), dict(d1)))
    w2 = (await ep("/bulk/send")(Req(k("kirim")), BP.BulkAksiRequest(ids=[str(A), str(Dd)])))["data"]
    harus("replay kirim: A replay, hasil sama, nol tulis", w2["items"][0]["replay"] is True and w2["items"][0]["result"] == w["items"][0]["result"]
          and (await baris(A))["sent_at"] == a1["sent_at"], w2["items"][0])

    # batal
    p = (await ep("/bulk/cancel/preview")(Req(), BP.BulkAksiRequest(ids=[str(A)])))["data"]["items"][0]
    harus("pratinjau batal tanpa alasan = ALASAN_WAJIB", [b["code"] for b in p["blocks"]] == ["ALASAN_WAJIB"], p)
    p = (await ep("/bulk/cancel/preview")(Req(), BP.BulkAksiRequest(ids=[str(A)], reason="x")))["data"]["items"][0]
    harus("pratinjau batal ber-alasan ok", p["ok"] and p["preview"]["status"] == "cancelled", p)
    harus("pratinjau batal nol tulis", (await baris(A))["status"] == "sent")
    w = (await ep("/bulk/cancel")(Req(k("batal")), BP.BulkAksiRequest(ids=[str(A)], reason=f"{PENANDA} batal massal")))["data"]
    a2 = await baris(A)
    harus("batal massal A", w["items"][0]["status"] == "ok" and (a2["status"], a2["cancelled_by"], a2["cancelled_reason"])
          == ("cancelled", U, f"{PENANDA} batal massal"), (w["items"][0], dict(a2)))
    w = (await ep("/bulk/cancel")(Req(k("batal2")), BP.BulkAksiRequest(ids=[str(A)], reason="lagi")))["data"]
    harus("batal ulang A ditolak", (w["items"][0]["status"], w["items"][0]["code"]) == ("rejected", "PO_TAK_BISA_DIBATALKAN"), w["items"][0])

    # hapus
    p = (await ep("/bulk/delete/preview")(Req(), BP.BulkAksiRequest(ids=[str(B), str(C), str(A)])))["data"]
    harus("pratinjau hapus [B,C,A]", [i["ok"] for i in p["items"]] == [True, True, False] and p["items"][2]["blocks"][0]["code"] == "PO_BUKAN_DRAF", p["items"])
    harus("pratinjau hapus nol tulis", (await baris(B)) is not None and (await baris(C)) is not None)
    w = (await ep("/bulk/delete")(Req(k("hapus")), BP.BulkAksiRequest(ids=[str(B), str(C), str(A)])))["data"]
    st = [(i["status"], i["code"]) for i in w["items"]]
    harus("hapus massal [B,C,A]", st == [("ok", None), ("ok", None), ("rejected", "PO_BUKAN_DRAF")], st)
    harus("B, C hilang; A tetap", (await baris(B)) is None and (await baris(C)) is None and (await baris(A)) is not None)

    async with pool.acquire() as c:
        dl = await c.fetch("""SELECT entity_id, "userId" FROM audit_logs WHERE tenant_id = $1 AND "eventType" = 'DOCUMENT_DELETED'
                              AND entity_type = 'purchase_orders' AND entity_id = ANY($2::uuid[])""", T, [B, C])
        harus("DOCUMENT_DELETED B,C ber-userId harness (V230)", sorted(str(r["entity_id"]) for r in dl) == sorted([str(B), str(C)])
              and all(r["userId"] == str(U) for r in dl), [dict(r) for r in dl])
        au = await c.fetch("SELECT \"eventType\" FROM audit_logs WHERE tenant_id = $1 AND \"eventType\" LIKE 'BULK_%' AND \"userId\" = $2",
                           T, str(U))  # U unik per jalan -> hanya audit harness ini
        jenis = [r["eventType"] for r in au]
        harus("audit BULK_SEND x2, BULK_CANCEL x2, BULK_DELETE x1", sorted(jenis) == sorted(["BULK_SEND", "BULK_SEND", "BULK_CANCEL",
                                                                                           "BULK_CANCEL", "BULK_DELETE"]), jenis)

    # bersih: D dibatalkan lewat rute tunggal
    r = await PO.cancel_purchase_order(Req(), Dd, PS.CancelPurchaseOrderRequest(reason=f"{PENANDA} bersih"))
    harus("bersih: D dibatalkan (tunggal)", r["data"]["status"] == "cancelled", r)
    async with pool.acquire() as c:
        sisa = await c.fetch("SELECT po_number, status FROM purchase_orders WHERE tenant_id = $1 AND notes LIKE $2 ORDER BY po_number",
                             T, f"{PENANDA}%")
    catat["sisa"] = [dict(r) for r in sisa]
    harus("sisa kaos = 2 PO Dibatalkan (A, D)", [r["status"] for r in sisa] == ["cancelled", "cancelled"], catat["sisa"])
    async with pool.acquire() as c:
        semua = list(ids.values())
        nj = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_id = ANY($2::uuid[])", T, semua)
        ni = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_id = ANY($2::uuid[])", T, semua)
    harus("nol jurnal + nol stok untuk 4 PO uji", (nj, ni) == (0, 0), (nj, ni))
    harus("nol panggilan kanal keluar (surel/WA/HTTP)", KELUAR == [], KELUAR)
    await pool.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
