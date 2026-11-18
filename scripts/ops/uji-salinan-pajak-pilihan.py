"""Pajak pilihan SATU penentu uji DB di SALINAN (bukan prod): handler ASLI GET /items/pajak-pilihan + penulis nama_pajak_item atas tax_codes klon, tenant kaos + nyawit. Hanya BACA.
A tiap jenis: daftar == hitungan SQL independen (aktif, arah cocok atau kosong); tanpa nonaktif
B SETIAP tax_code tenant: ada di daftar <=> penulis (nama_pajak_item) menerimanya (uji silang)
C bentuk {id,kode,nama,tarif,text}; text tanpa tarif ganda"""
import asyncio
import sys

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.services import pajak_sah as PS  # noqa: E402

TENANT = ("pt-kita-nyawit-bersama", "kaos-biru-konveksi")
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "uji-salinan", "username": "uji-salinan", "role": "OWNER"}
    return r


async def main():
    conn = await IT.get_db_connection()
    for tid in TENANT:
        semua = [dict(r) for r in await conn.fetch("SELECT id::text AS id, code, name, rate, direction, is_active FROM tax_codes WHERE tenant_id = $1", tid)]
        cek(f"{tid} tax_codes terbaca", len(semua) > 0, f"{len(semua)} baris")
        for jenis, arah in PS.JENIS.items():
            ids = [p["id"] for p in (await IT.pajak_pilihan(req(tid), jenis=jenis))["pajak"]]
            sql_ids = {r["id"] for r in await conn.fetch(
                "SELECT id::text AS id FROM tax_codes WHERE tenant_id = $1 AND is_active = true AND (COALESCE(direction, '') = '' OR direction = $2)", tid, arah)}
            cek(f"A {tid} {jenis}: himpunan == SQL independen", set(ids) == sql_ids, f"{len(ids)} vs {len(sql_ids)}")
            salah = []
            for r in semua:
                try:
                    await IT.nama_pajak_item(conn, tid, r["id"], "f", arah)
                    diterima = True
                except HTTPException:
                    diterima = False
                if diterima != (r["id"] in ids):
                    salah.append(r["code"])
            cek(f"B {tid} {jenis}: daftar ≡ penulis untuk SEMUA {len(semua)} pajak", not salah, ",".join(salah[:5]))
            p = (await IT.pajak_pilihan(req(tid), jenis=jenis))["pajak"]
            ok = all(set(x) == {"id", "kode", "nama", "tarif", "text"} and x["text"] and x["text"].count("%") <= 1 for x in p)
            cek(f"C {tid} {jenis}: bentuk + text tanpa tarif ganda", ok, f"{[x['text'] for x in p][:4]}")
    await conn.close()
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
