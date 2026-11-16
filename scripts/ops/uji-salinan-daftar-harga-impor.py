"""Daftar Harga IMPOR/EKSPOR Excel (BUG-251) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhimp*.
V500 (harga berjangka) dipasang di salinan lebih dulu (cabang ini turunan 245).
I1 ekspor daftar berisi -> baca ulang -> pratinjau: SEMUA 'tetap' (satuan kosong == satuan dasar; tak menggandakan baris).
I2 pratinjau READ ONLY: baris + harga di DB tak berubah.
I3 ubah harga di berkas -> tulis: baris YANG SAMA berubah, jumlah baris tetap; kolom Berakhir tak ada = akhir lama utuh.
I4 beda tanggal mulai = baris berjangka BARU (tambah), harga lama utuh.
I5 satu baris salah (kode tak ada) = 422 IMPOR_DITOLAK, NOL tulis (jumlah + harga tak berubah).
I6 kunci idempoten: tulis ulang kunci sama = respons asli, tak menambah baris; tanpa kunci = 400 IMPOR_KUNCI_WAJIB.
I7 ekspor csv id-ID (BOM, ';'); daftar tenant lain 404."""
import asyncio
import io
import os
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.datastructures import UploadFile
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate  # noqa: E402
from app.services import impor_harga as IH  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)
MIG = __import__("pathlib").Path("/app/backend/migrations")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, kunci=None, method="POST"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-dh-impor"}
    return r


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else getattr(e, "code", None)), e.status_code
    return None


async def baris(pool, tid, pl):
    async with pool.acquire() as c:
        return [(r["item_code"], r["unit"], r["min_quantity"], r["price"], r["start_date"], r["end_date"]) for r in await c.fetch(
            """SELECT pli.item_code, pli.unit, pli.min_quantity, pli.price, pli.start_date, pli.end_date FROM price_list_items pli
               JOIN price_lists p ON p.id = pli.price_list_id WHERE p.tenant_id = $1 AND pli.price_list_id = $2::uuid
               ORDER BY pli.item_code, pli.min_quantity, pli.start_date NULLS FIRST""", tid, pl)]


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetch("""SELECT id, COALESCE(item_code, sku) AS kode FROM products p WHERE tenant_id = $1 AND status = 'active'
                               AND COALESCE(for_sales, true) AND deleted_at IS NULL AND COALESCE(item_code, sku) IS NOT NULL
                               AND (SELECT count(*) FROM products q WHERE q.tenant_id = $1 AND (lower(q.item_code) = lower(COALESCE(p.item_code, p.sku))
                                    OR lower(q.sku) = lower(COALESCE(p.item_code, p.sku)))) = 1 ORDER BY id LIMIT 2""", tid)
    if len(brg) < 2:
        print(f"INFO {tid}: kurang dari 2 barang berkode unik -- dilewati")
        return None
    r = await PL.create_price_list(req(tid, f"uji-imp-buat-{st}"), CreatePriceListRequest(code=f"UJI-IMP-{st}", name="UJI impor"))
    pl = uuid.UUID(str(r["data"]["id"]))
    for b, harga in ((brg[0], 1000), (brg[1], 2000)):
        await PL.add_item_to_price_list(req(tid), pl, PriceListItemCreate(item_id=str(b["id"]), price=harga, min_quantity=1))
    await PL.add_item_to_price_list(req(tid), pl, PriceListItemCreate(item_id=str(brg[0]["id"]), price=900, min_quantity=10,
                                                                      end_date=date.today() + timedelta(days=90)))
    sebelum = await baris(pool, tid, str(pl))
    # ---- I1 round trip
    resp = await PL.ekspor_harga(req(tid, method="GET"), pl, "xlsx")
    baca = IH.baca_xlsx(resp.body)
    prat = await PL.pratinjau_impor_harga(req(tid), pl, {"baris": baca["baris"]})
    d = prat["data"]
    cek(f"{tid} I1 ekspor->baca->pratinjau: {len(baca['baris'])} baris semua 'tetap', ok, tak menggandakan",
        d["ok"] and [x["aksi"] for x in d["baris"]] == ["tetap"] * 3 and d["ringkas"]["tetap"] == 3, f"{d['ringkas']}")
    cek(f"{tid} I2 pratinjau READ ONLY: baris + harga tak berubah", await baris(pool, tid, str(pl)) == sebelum)
    # ---- I3 ubah harga
    ubah = [dict(x) for x in baca["baris"]]
    ubah[0]["harga"] = 1500
    for x in ubah:
        x.pop("akhir", None)                      # kolom Berakhir tak ada -> akhir lama utuh
    p2 = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": ubah}))["data"]
    w = await PL.impor_harga(req(tid, f"uji-imp-1-{st}"), pl, p2["payload"], Response())
    s2 = await baris(pool, tid, str(pl))
    akhir_utuh = [x[5] for x in s2] == [x[5] for x in sebelum]
    cek(f"{tid} I3 tulis ubah: satu baris berubah harga, jumlah baris tetap, akhir lama utuh",
        w["data"]["ubah"] == 1 and len(s2) == len(sebelum) and sum(1 for a, b in zip(s2, sebelum) if a != b) == 1 and akhir_utuh, f"{w['data']}")
    # ---- I4 berjangka
    t1 = date.today() + timedelta(days=30)
    berjangka = [{"kode": baca["baris"][0]["kode"], "satuan": baca["baris"][0]["satuan"], "harga": 1800, "mulai": t1.isoformat()}]
    p3 = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": berjangka}))["data"]
    await PL.impor_harga(req(tid, f"uji-imp-2-{st}"), pl, p3["payload"], Response())
    s3 = await baris(pool, tid, str(pl))
    cek(f"{tid} I4 beda tanggal mulai = baris berjangka baru (tambah), baris lama utuh",
        p3["baris"][0]["aksi"] == "tambah" and len(s3) == len(s2) + 1 and all(x in s3 for x in s2) and any(x[4] == t1 and x[3] == 1800 for x in s3))
    # ---- I5 atomik
    jelek = ubah + [{"kode": "TIDAK-ADA-" + str(st), "harga": 5}]
    p4 = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": jelek}))["data"]
    e = await galat(PL.impor_harga(req(tid, f"uji-imp-3-{st}"), pl, p4["payload"], Response()))
    cek(f"{tid} I5 satu baris salah: pratinjau ok=false, tulis 422 IMPOR_DITOLAK, NOL tulis",
        not p4["ok"] and e == ("IMPOR_DITOLAK", 422) and await baris(pool, tid, str(pl)) == s3, f"{e}")
    # ---- I6 idempoten
    k = f"uji-imp-4-{st}"
    baru = [{"kode": baca["baris"][1]["kode"], "satuan": baca["baris"][1]["satuan"], "harga": 2100}]
    p5 = (await PL.pratinjau_impor_harga(req(tid), pl, {"baris": baru}))["data"]
    a = await PL.impor_harga(req(tid, k), pl, p5["payload"], Response())
    n_a = len(await baris(pool, tid, str(pl)))
    b = await PL.impor_harga(req(tid, k), pl, p5["payload"], Response())
    tanpa = await galat(PL.impor_harga(req(tid), pl, p5["payload"], Response()))
    cek(f"{tid} I6 kunci sama = respons asli tanpa baris baru; tanpa kunci = 400 IMPOR_KUNCI_WAJIB",
        a == b and len(await baris(pool, tid, str(pl))) == n_a and tanpa == ("IMPOR_KUNCI_WAJIB", 400), f"{tanpa}")
    # ---- I7 csv + tenant lain
    csv = await PL.ekspor_harga(req(tid, method="GET"), pl, "csv")
    lain = [t for t in TENANT if t != tid]
    asing = await galat(PL.ekspor_harga(req(lain[0] if lain else "tenant-tak-ada", method="GET"), pl, "xlsx"))
    cek(f"{tid} I7 csv id-ID (BOM, ';') + daftar tenant lain 404",
        csv.body.startswith(b"\xef\xbb\xbf") and b";" in csv.body.split(b"\r\n")[0] and asing and asing[1] == 404, f"{asing}")
    return pl


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhimp"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())   # puncak rantai: penulis baris/resolve membaca min_price
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
