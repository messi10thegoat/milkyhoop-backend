"""Daftar Harga RIWAYAT perubahan (BUG-252) -- uji di SALINAN (klon milkydb); tulis HANYA di salinan. GUARD: pool harus scratch_dhrwy*.
V500 + V501 dipasang di salinan (cabang turunan 245/040). audit_logs ASLI (trigger immutable ikut ter-klon).
R1 tambah baris -> 1 event TAMBAH_BARIS (entity_id = daftar, dari null, ke berisi harga); R2 PATCH -> UBAH_BARIS dari->ke; R3 hapus baris -> HAPUS_BARIS (dari berisi).
R4 impor 251 -> SATU event IMPOR (baris berubah saja, tak ada baris 'tetap'); R5 ubah massal D3 -> SATU event UBAH_MASSAL berbaris.
R6 header/status/bawaan -> event; hapus daftar -> event HAPUS dan GET riwayat tetap membaca riwayat daftar yang sudah dihapus.
R7 GET riwayat: terbaru dulu, pelaku (User.name) terisi, filter aksi, total == hitungan SQL; tenant lain = kosong/404.
R8 IMMUTABLE: UPDATE dan DELETE pada baris riwayat DITOLAK trigger."""
import asyncio
import os
import pathlib
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bulk_price_lists as BP  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402
from app.schemas.price_lists import CreatePriceListRequest, PriceListItemCreate, PriceListItemUpdate, UpdatePriceListRequest  # noqa: E402
from app.services import harga_riwayat as HR  # noqa: E402
from app.services import impor_harga as IH  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

HASIL = []
TENANT = tuple(x for x in os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(",") if x)
MIG = pathlib.Path("/app/backend/migrations")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None, method="POST"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": uid, "username": "uji-dh-rwy"}
    return r


async def galat(coro):
    try:
        await coro
    except HTTPException as e:
        d = e.detail
        return (d.get("code") if isinstance(d, dict) else getattr(e, "code", None)), e.status_code
    except Exception as e:  # noqa: BLE001
        return (type(e).__name__, str(e)[:120])
    return None


async def baca(tid, uid, pl, **kw):
    kw = {"limit": 50, "offset": 0, "aksi": None, "dari": None, "sampai": None, **kw}
    return (await BP.riwayat_daftar(req(tid, uid, method="GET"), pl, **kw))["data"]


async def periksa(pool, tid):
    st = int(time.time())
    async with pool.acquire() as c:
        brg = await c.fetchrow("""SELECT id, COALESCE(item_code, sku) AS kode FROM products p WHERE tenant_id = $1 AND status = 'active' AND COALESCE(for_sales, true)
                                  AND deleted_at IS NULL AND COALESCE(item_code, sku) IS NOT NULL
                                  AND (SELECT count(*) FROM products q WHERE q.tenant_id = $1 AND (lower(q.item_code) = lower(COALESCE(p.item_code, p.sku))
                                       OR lower(q.sku) = lower(COALESCE(p.item_code, p.sku)))) = 1 ORDER BY id LIMIT 1""", tid)
        user = await c.fetchrow('SELECT id, name FROM "User" WHERE "tenantId" = $1 ORDER BY id LIMIT 1', tid)
    if brg is None or user is None:
        print(f"INFO {tid}: tak ada barang berkode unik / pengguna -- dilewati")
        return
    uid = user["id"]
    r = await PL.create_price_list(req(tid, uid, f"uji-rwy-buat-{st}"), CreatePriceListRequest(code=f"UJI-RWY-{st}", name="UJI riwayat"))
    pl = uuid.UUID(str(r["data"]["id"]))
    # ---- R1..R3
    t = await PL.add_item_to_price_list(req(tid, uid), pl, PriceListItemCreate(item_id=str(brg["id"]), price=10000, min_quantity=1))
    line = uuid.UUID(str(t["data"]["id"]))
    await PL.update_price_list_item(req(tid, uid), pl, line, PriceListItemUpdate(price=11000, min_price=9000, discount_percent=5))
    d = await baca(tid, uid, pl)
    tamb = [i for i in d["items"] if i["aksi"] == "TAMBAH_BARIS"]
    ubah = [i for i in d["items"] if i["aksi"] == "UBAH_BARIS"]
    cek(f"{tid} R1 tambah baris: 1 event, dari null, ke harga 10000; entity = daftar",
        len(tamb) == 1 and tamb[0]["baris"][0]["dari"] is None and tamb[0]["baris"][0]["ke"]["harga"] == 10000.0, f"{[i['aksi'] for i in d['items']]}")
    cek(f"{tid} R2 PATCH: dari 10000/-/- -> ke 11000/9000/5; ringkas menyebut perubahan",
        len(ubah) == 1 and ubah[0]["baris"][0]["dari"]["harga"] == 10000.0 and ubah[0]["baris"][0]["ke"]["harga"] == 11000.0 and ubah[0]["baris"][0]["ke"]["min_price"] == 9000.0
        and ubah[0]["baris"][0]["ke"]["diskon_persen"] == 5.0 and "→" in ubah[0]["ringkas"], f"{ubah[0]['ringkas'] if ubah else None}")
    # ---- R4 impor (ubah harga 1 baris via berkas + 1 baris baru qty 5)
    ekspor = await PL.ekspor_harga(req(tid, uid, method="GET"), pl, "xlsx")
    baris = IH.baca_xlsx(ekspor.body)["baris"]
    baris[0]["harga"] = 11500
    baris.append({"kode": baris[0]["kode"], "satuan": baris[0]["satuan"], "min_qty": 5, "harga": 10500})
    p = (await PL.pratinjau_impor_harga(req(tid, uid), pl, {"baris": baris}))["data"]
    await PL.impor_harga(req(tid, uid, f"uji-rwy-imp-{st}"), pl, p["payload"], Response())
    imp = [i for i in (await baca(tid, uid, pl))["items"] if i["aksi"] == "IMPOR"]
    cek(f"{tid} R4 impor: SATU event IMPOR berisi 2 baris berubah (1 ubah + 1 tambah), tanpa baris tetap", len(imp) == 1 and imp[0]["jumlah"] == 2 and len(imp[0]["baris"]) == 2, f"{[ (len(i['baris']), i['jumlah']) for i in imp]}")
    # ---- R5 massal
    B = BP.BulkHargaRequest
    await BP.bulk_price(req(tid, uid, f"uji-rwy-d3-{st}"), pl, B(mode="persen", nilai=Decimal("10")))
    mas = [i for i in (await baca(tid, uid, pl))["items"] if i["aksi"] == "UBAH_MASSAL"]
    cek(f"{tid} R5 ubah massal: SATU event UBAH_MASSAL berbaris (harga lama != baru)", len(mas) == 1 and len(mas[0]["baris"]) >= 2 and all(b["dari"]["harga"] != b["ke"]["harga"] for b in mas[0]["baris"]), f"{len(mas[0]['baris']) if mas else None}")
    # ---- R3 hapus baris
    await PL.remove_item_from_price_list(req(tid, uid), pl, line)
    hb = [i for i in (await baca(tid, uid, pl))["items"] if i["aksi"] == "HAPUS_BARIS"]
    cek(f"{tid} R3 hapus baris: event HAPUS_BARIS dengan nilai lama", len(hb) == 1 and hb[0]["baris"][0]["dari"] is not None and hb[0]["baris"][0]["ke"] is None)
    # ---- R6 header/status
    await PL.update_price_list(req(tid, uid), pl, UpdatePriceListRequest(name="UJI riwayat 2"))
    await PL.ubah_status_daftar(req(tid, uid, f"uji-rwy-st-{st}"), pl, {"aktif": False})
    d6 = await baca(tid, uid, pl)
    aksi = [i["aksi"] for i in d6["items"]]
    cek(f"{tid} R6a header + status tercatat", "UBAH_HEADER" in aksi and "STATUS" in aksi, f"{aksi}")
    # ---- R7 GET: urutan, pelaku, filter, total
    waktu = [i["waktu"] for i in d6["items"]]
    async with pool.acquire() as c:
        n_db = await c.fetchval("""SELECT count(*) FROM audit_logs a WHERE a.tenant_id = $1 AND a.entity_type = 'price_list' AND a.entity_id = $2::uuid""", tid, str(pl))
    f = await baca(tid, uid, pl, aksi="UBAH_BARIS")
    cek(f"{tid} R7 GET riwayat: terbaru dulu, pelaku = {user['name']!r}, total == SQL ({n_db}), filter aksi",
        waktu == sorted(waktu, reverse=True) and d6["items"][0]["oleh"]["nama"] == user["name"] and d6["total"] == n_db and len(f["items"]) == 1 and d6["tercatat_sejak"] is not None,
        f"total={d6['total']} db={n_db} oleh={d6['items'][0]['oleh']}")
    asing = await galat(baca("tenant-tak-ada", uid, pl))
    cek(f"{tid} R7b tenant lain: riwayat daftar ini 404 (tak bocor)", asing == (None, 404) or (asing and asing[1] == 404), f"{asing}")
    # ---- R8 immutable
    async with pool.acquire() as c:
        e1 = await galat(c.execute("""UPDATE audit_logs SET metadata = '{}'::jsonb WHERE tenant_id = $1 AND entity_type = 'price_list' AND entity_id = $2::uuid""", tid, str(pl)))
        e2 = await galat(c.execute("""DELETE FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'price_list' AND entity_id = $2::uuid""", tid, str(pl)))
    cek(f"{tid} R8 immutable: UPDATE dan DELETE riwayat ditolak trigger", e1 is not None and e2 is not None, f"{e1} | {e2}")
    # ---- R6b hapus daftar -> riwayat tetap terbaca
    await PL.delete_price_list(req(tid, uid, f"uji-rwy-hp-{st}"), pl)
    d7 = await baca(tid, uid, pl)
    cek(f"{tid} R6b hapus daftar: event HAPUS dan riwayat daftar yang sudah dihapus tetap terbaca", d7["items"][0]["aksi"] == "HAPUS" and d7["total"] == d6["total"] + 1, f"{d7['items'][0]['aksi']} {d7['total']}")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
        if not db.startswith("scratch_dhrwy"):
            print(f"TOLAK: bukan salinan (pool={db})")
            return 2
        await c.execute((MIG / "V500__daftar_harga_berjangka.sql").read_text())
        await c.execute((MIG / "V501__price_list_items_min_price.sql").read_text())
    for tid in TENANT:
        await periksa(pool, tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
