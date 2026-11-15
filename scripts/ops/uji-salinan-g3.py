"""Gudang G3 uji DB di SALINAN (bukan prod): handler ASLI pratinjau + penulis gudang atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A pratinjau buat read-only (nol baris baru) + kode otomatis GDG-NNN; buat nyata == pratinjau; X-Idempotency-Key: ulang = id sama, satu baris
B kode ganda -> GalatBerkode 400 GUDANG_KODE_SUDAH_ADA (field code); PATCH: null tak diubah, "" mengosongkan alamat (NULL di DB)
C status: nonaktif gudang BER-STOK = peringatan (pratinjau ok, POST /status jalan; DELETE/PATCH lama TETAP 400); gudang UTAMA nonaktif = blok 400 (pratinjau == penulis); aktifkan; sudah aktif = blok
D jadikan utama: pratinjau memuat GUDANG_UTAMA_DIGANTI; penulis memindahkan; tepat satu utama; Transit (bila ada) = GUDANG_SISTEM 409 di ubah/status/utama
E jenis_text/jenis_catatan di daftar + detail; aksi_tersedia.nonaktifkan: ber-stok tersedia, utama tidak"""
import asyncio
import sys
import uuid

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import warehouses as WH  # noqa: E402
from app.schemas.warehouses import CreateWarehouseRequest, SetWarehouseStatusRequest, UpdateWarehouseRequest  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid, kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": h})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


def kode(e):
    return getattr(e, "code", None)


def isi(r):
    return r["data"] if isinstance(r, dict) else r.data


def gid(d):
    return str(d["id"] if isinstance(d, dict) else d.id)


async def main():
    pool = await WH.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik", uid is not None)
            if uid is None:
                continue
            rq = req(tid, uid)

            async def n_gudang():
                return await c.fetchval("/* tenant_id */ SELECT count(*) FROM warehouses WHERE tenant_id = $1", tid)

            async def baris(i):
                return await c.fetchrow("/* tenant_id */ SELECT * FROM warehouses WHERE tenant_id = $1 AND id = $2::uuid", tid, str(i))

            # A
            n0 = await n_gudang()
            body = CreateWarehouseRequest(name="UJI G3 Gudang", address="Jl. Uji 1", phone="0812", is_branch=True)
            pv = (await WH.preview_create_warehouse(rq, body))["data"]
            cek(f"A {tid} pratinjau buat: ok, kode otomatis GDG-NNN, jenis Cabang, NOL baris baru", pv["ok"] is True and pv["rencana"]["gudang"]["kode"].startswith("GDG-")
                and pv["rencana"]["jenis_text"] == "Cabang" and await n_gudang() == n0, f"{pv['rencana']['gudang']}")
            kunci = f"uji-g3-{uuid.uuid4()}"
            r1 = await galat(WH.create_warehouse(req(tid, uid, kunci), body))
            if isinstance(r1, HTTPException):
                cek(f"A {tid} buat", False, f"{r1.status_code} {r1.detail}")
                continue
            d1 = isi(r1)
            r2 = await galat(WH.create_warehouse(req(tid, uid, kunci), body))
            cek(f"A {tid} buat nyata == pratinjau (kode {gid(d1)[:8]}), idempoten: ulang ber-kunci = id sama, satu baris baru", (d1["code"] if isinstance(d1, dict) else d1.code) == pv["rencana"]["gudang"]["kode"]
                and gid(isi(r2)) == gid(d1) and await n_gudang() == n0 + 1, f"{n0} -> {await n_gudang()}")
            baru = gid(d1)
            kode_baru = (d1["code"] if isinstance(d1, dict) else d1.code)
            # B
            dup = await galat(WH.create_warehouse(rq, CreateWarehouseRequest(name="UJI G3 dobel", code=kode_baru)))
            cek(f"B {tid} kode ganda -> 400 GUDANG_KODE_SUDAH_ADA field code, nol baris baru", isinstance(dup, HTTPException) and dup.status_code == 400 and kode(dup) == "GUDANG_KODE_SUDAH_ADA"
                and dup.badan()["field"] == "code" and await n_gudang() == n0 + 1)
            await WH.update_warehouse(rq, uuid.UUID(baru), UpdateWarehouseRequest(name="UJI G3 Gudang", address="", city="Kota Uji", phone=None))
            w = await baris(baru)
            cek(f"B {tid} PATCH: \"\" mengosongkan alamat (NULL), null tak diubah (telepon tetap), kota terisi", w["address"] is None and w["phone"] == "0812" and w["city"] == "Kota Uji", f"{w['address']!r} {w['phone']!r}")
            # C
            utama = await c.fetchrow("/* tenant_id */ SELECT id, name FROM warehouses WHERE tenant_id = $1 AND is_default AND is_active AND NOT is_system LIMIT 1", tid)
            if utama is not None:
                pu = (await WH.preview_status_warehouse(rq, utama["id"], SetWarehouseStatusRequest(aktif=False)))["data"]
                wu = await galat(WH.set_warehouse_status(rq, utama["id"], SetWarehouseStatusRequest(aktif=False)))
                cek(f"C {tid} nonaktif gudang UTAMA = blok GUDANG_UTAMA_TAK_BISA_NONAKTIF (pratinjau == penulis, 400)", pu["ok"] is False and pu["blocks"][0]["code"] == "GUDANG_UTAMA_TAK_BISA_NONAKTIF"
                    and isinstance(wu, HTTPException) and wu.status_code == 400 and kode(wu) == "GUDANG_UTAMA_TAK_BISA_NONAKTIF" and wu.detail == "Jadikan gudang lain sebagai Utama dulu." and (await baris(utama["id"]))["is_active"])
            berstok = await c.fetchrow("""/* tenant_id */ SELECT w.id, w.name FROM warehouses w WHERE w.tenant_id = $1 AND w.is_active AND NOT w.is_default AND NOT w.is_system AND w.id <> $2::uuid
                AND EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id = w.tenant_id AND il.warehouse_id = w.id GROUP BY il.product_id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) <> 0) LIMIT 1""", tid, baru)
            if berstok is not None:
                pb = (await WH.preview_status_warehouse(rq, berstok["id"], SetWarehouseStatusRequest(aktif=False)))["data"]
                akt = (await WH.get_warehouse(rq, berstok["id"])).data.aksi_tersedia
                cek(f"C {tid} nonaktif gudang BER-STOK = pratinjau ok + peringatan GUDANG_BERSTOK_DINONAKTIFKAN (butuh_konfirmasi); aksi_tersedia.nonaktifkan tersedia",
                    pb["ok"] is True and [x["code"] for x in pb["peringatan"]] == ["GUDANG_BERSTOK_DINONAKTIFKAN"] and pb["peringatan"][0]["butuh_konfirmasi"] is True and akt["nonaktifkan"].tersedia is True, f"{pb['peringatan']}")
                dl = await galat(WH.delete_warehouse(rq, berstok["id"]))
                pl = await galat(WH.update_warehouse(rq, berstok["id"], UpdateWarehouseRequest(is_active=False)))
                cek(f"C {tid} jalur LAMA: DELETE dan PATCH is_active:false pada gudang ber-stok TETAP 400 GUDANG_BERSTOK (kalimat Indonesia), tetap aktif",
                    all(isinstance(e, HTTPException) and e.status_code == 400 and kode(e) == "GUDANG_BERSTOK" and e.detail.startswith("Gudang masih berisi stok (") for e in (dl, pl)) and (await baris(berstok["id"]))["is_active"], f"{getattr(dl, 'detail', dl)}")
                ws = await galat(WH.set_warehouse_status(req(tid, uid, f"uji-g3-{uuid.uuid4()}"), berstok["id"], SetWarehouseStatusRequest(aktif=False)))
                cek(f"C {tid} penulis menonaktifkan gudang ber-stok (di klon): is_active false", not isinstance(ws, HTTPException) and not (await baris(berstok["id"]))["is_active"], f"{ws if isinstance(ws, HTTPException) else ''}")
                await WH.set_warehouse_status(rq, berstok["id"], SetWarehouseStatusRequest(aktif=True))
                cek(f"C {tid} aktifkan lagi: is_active true", (await baris(berstok["id"]))["is_active"])
            else:
                print(f"CATATAN C {tid}: tak ada gudang bukan-utama ber-stok di klon; peringatan ber-stok diuji di unit", flush=True)
            sa = await galat(WH.set_warehouse_status(rq, uuid.UUID(baru), SetWarehouseStatusRequest(aktif=True)))
            cek(f"C {tid} aktifkan gudang yang sudah aktif = blok GUDANG_SUDAH_AKTIF", isinstance(sa, HTTPException) and kode(sa) == "GUDANG_SUDAH_AKTIF")
            sn = await WH.set_warehouse_status(rq, uuid.UUID(baru), SetWarehouseStatusRequest(aktif=False))
            cek(f"C {tid} nonaktif gudang baru kosong: sukses (tanpa stok = tanpa peringatan)", not (await baris(baru))["is_active"] and isi(sn)["is_active"] is False)
            await WH.set_warehouse_status(rq, uuid.UUID(baru), SetWarehouseStatusRequest(aktif=True))
            # D
            if utama is not None:
                pd = (await WH.preview_set_default_warehouse(rq, uuid.UUID(baru)))["data"]
                cek(f"D {tid} pratinjau jadikan utama memuat GUDANG_UTAMA_DIGANTI ({utama['name']})", pd["ok"] is True and [x["code"] for x in pd["peringatan"]] == ["GUDANG_UTAMA_DIGANTI"] and utama["name"] in pd["peringatan"][0]["message"])
                await WH.set_default_warehouse(rq, uuid.UUID(baru))
                n_utama = await c.fetchval("/* tenant_id */ SELECT count(*) FROM warehouses WHERE tenant_id = $1 AND is_default AND NOT is_system", tid)
                cek(f"D {tid} penulis memindahkan utama: tepat satu utama, = gudang baru", n_utama == 1 and (await baris(baru))["is_default"] and not (await baris(utama["id"]))["is_default"])
            tr = await c.fetchval("/* tenant_id */ SELECT id FROM warehouses WHERE tenant_id = $1 AND is_system LIMIT 1", tid)
            if tr is not None:
                es = [await galat(WH.update_warehouse(rq, tr, UpdateWarehouseRequest(name="X"))), await galat(WH.set_warehouse_status(rq, tr, SetWarehouseStatusRequest(aktif=False))),
                      await galat(WH.set_default_warehouse(rq, tr))]
                cek(f"D {tid} gudang sistem Transit: ubah/status/utama = GUDANG_SISTEM 409", all(isinstance(e, HTTPException) and e.status_code == 409 and kode(e) == "GUDANG_SISTEM" for e in es), f"{[getattr(e, 'detail', e) for e in es]}")
            else:
                print(f"CATATAN D {tid}: tak ada gudang sistem Transit di klon; diuji di unit", flush=True)
            # E
            lst = await WH.list_warehouses(rq, skip=0, limit=100, search=None, is_active=None, is_branch=None, aksi=None, with_totals=False)
            dt = (await WH.get_warehouse(rq, uuid.UUID(baru))).data
            cek(f"E {tid} jenis_text/jenis_catatan di daftar dan detail (Cabang + catatan; biasa tanpa catatan)", all(g.jenis_text in ("Cabang", "Gudang biasa") for g in lst.data)
                and dt.jenis_text == "Cabang" and dt.jenis_catatan and any(g.jenis_text == "Gudang biasa" and g.jenis_catatan is None for g in lst.data), f"{dt.jenis_text}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
