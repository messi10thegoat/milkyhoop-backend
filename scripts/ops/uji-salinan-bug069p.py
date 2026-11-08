"""BUG-069 REOPEN (ROW p) -- uji di SALINAN (klon milkydb), BACA saja, per tenant (kaos, nyawit):
T1 rute ASLI GET /bills/{id} dan /bills/v2/{id} mengirim aksi_tersedia.nota_debit {tersedia, alasan} (bentuk AksiTersedia) utk SEMUA faktur.
T2 faktur LUNAS terposting (mis. nyawit BILL-2611-0003/0008) -> tersedia True, alasan None.
T3 PARITAS dengan pratinjau ND dari faktur (rakit_dari_tagihan, nol tulis): tersedia == tak ada blok kepala di pratinjau; alasan == pesan
   blok kepala pertama. Draf/batal -> tak tersedia beralasan."""
import asyncio
import os
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bills as RB, vendor_credits as RVC  # noqa: E402
from app.services import nota_debit as ND  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
KEPALA = {"ND_TAGIHAN_TAK_AKTIF", "ND_PAJAK_INKLUSIF_BELUM_DIDUKUNG", "ND_DPP_MANUAL_BELUM_DIDUKUNG", "ND_DISKON_DOKUMEN_BER_PPN_BELUM"}


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/bills", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug069p"}
    return r


async def periksa(conn, tid):
    bills = await conn.fetch("SELECT id, invoice_number, status_v2 FROM bills WHERE tenant_id = $1 ORDER BY invoice_number", tid)
    status = {}
    salah_bentuk, salah_paritas, v1v2 = [], [], []
    for b in bills:
        r1 = await RB.get_bill_detail(req(tid), b["id"])
        r2 = await RB.get_bill_v2(req(tid), b["id"])
        a1, a2 = r1.get("aksi_tersedia"), r2.get("aksi_tersedia")
        if a1 != a2:
            v1v2.append(b["invoice_number"])
        nd = (a1 or {}).get("nota_debit")
        if not nd or set(nd) != {"tersedia", "alasan"} or (nd["tersedia"] and nd["alasan"] is not None) or (not nd["tersedia"] and not nd["alasan"]):
            salah_bentuk.append(b["invoice_number"])
            continue
        pv = await RVC.rakit_dari_tagihan(conn, tid, RVC.VendorCreditPreviewRequest(original_bill_id=str(b["id"]), reason="return"))
        kepala = [x for x in pv["blocks"] if x["code"] in KEPALA]
        if nd["tersedia"] != (not kepala) or (kepala and nd["alasan"] != kepala[0]["message"]):
            salah_paritas.append(f"{b['invoice_number']}({b['status_v2']}): aksi={nd} pratinjau={[x['code'] for x in kepala]}")
        status.setdefault((b["status_v2"], nd["tersedia"]), []).append(b["invoice_number"])
    cek(f"{tid} T1 bentuk aksi_tersedia.nota_debit benar di semua faktur", not salah_bentuk, f"n={len(bills)} salah={salah_bentuk[:5]}")
    cek(f"{tid} T1 v1 == v2", not v1v2, f"beda={v1v2[:5]}")
    cek(f"{tid} T3 paritas aksi == blok kepala pratinjau ND", not salah_paritas, "; ".join(salah_paritas[:3]))
    print(f"[info] {tid}: (status, tersedia) -> jumlah: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(status.items())), flush=True)
    lunas = [b for b in bills if b["status_v2"] == "paid"]
    cek(f"{tid} T2 faktur LUNAS menawarkan nota debit",
        all(n in status.get(("paid", True), []) for n in [b["invoice_number"] for b in lunas]), f"lunas={len(lunas)}")
    if tid == "pt-kita-nyawit-bersama":
        for no in ("BILL-2611-0003", "BILL-2611-0008"):
            cek(f"{tid} {no} tersedia nota debit", no in status.get(("paid", True), []) + status.get(("posted", True), []))
    for b in bills:
        if b["status_v2"] in ("draft", "void"):
            cek(f"{tid} draf/batal tak tersedia beralasan ({b['invoice_number']})", b["invoice_number"] in status.get((b["status_v2"], False), []))
            break


async def utama():
    pool = await RB.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b069p"):
            print(f"TOLAK: bukan salinan ({db})")
            return 2
        for tid in TENANT:
            await periksa(conn, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
