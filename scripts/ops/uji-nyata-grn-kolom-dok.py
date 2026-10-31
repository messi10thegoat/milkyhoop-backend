"""Harness NYATA L5b -- BACA SAJA (default_transaction_read_only=on, diverifikasi): daftar GET /api/goods-receipts (handler nyata) atas kaos:
tiap baris documents[]/doc_count == jenis dokumen bundel detail GET /{id}/documents (tanpa rekap); jumlah kueri daftar TETAP (count + baris +
2 batch) berapa pun barisnya; contoh respons nyata untuk FRONTEND. Nol tulis. Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import asyncpg

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "/app")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

from app.routers import goods_receipts as GR  # noqa: E402

T = "kaos-biru-konveksi"


async def main(keluar):
    pool = await asyncpg.create_pool(os.environ["URL"], min_size=1, max_size=2, server_settings={"default_transaction_read_only": "on"})
    cek, catat, kueri = [], {}, []

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    class Hitung:
        def __init__(s, c): s.c = c
        def __getattr__(s, a): return getattr(s.c, a)
        async def fetch(s, *a, **k): kueri.append(a[0][:60]); return await s.c.fetch(*a, **k)
        async def fetchval(s, *a, **k): kueri.append(a[0][:60]); return await s.c.fetchval(*a, **k)
        async def fetchrow(s, *a, **k): kueri.append(a[0][:60]); return await s.c.fetchrow(*a, **k)

    class CM:
        async def __aenter__(s):
            s.k = await pool.acquire()
            return Hitung(s.k)
        async def __aexit__(s, *e):
            await pool.release(s.k)

    class P:
        def acquire(s): return CM()

    async def gp(): return P()
    GR.get_pool = gp
    async with pool.acquire() as c:
        harus("koneksi BACA SAJA", await c.fetchval("SHOW default_transaction_read_only") == "on")
        uid = await c.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                                  WHERE utr.tenant_id = $1 AND r.code = 'OWNER' LIMIT 1""", T)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uid), "role": "OWNER"}), headers={})
    kueri.clear()
    d = (await GR.list_goods_receipts(req, None, None, None, 50, 0))["data"]
    n_kueri = len(kueri)
    items = d["items"]
    harus("daftar GRN kaos tidak kosong (kontrol)", len(items) >= 1, d["total"])
    harus("jumlah kueri daftar = 4 (count + baris + 2 batch), bukan N+1", n_kueri == 4, [n_kueri, kueri])
    for it in items:
        b = await GR.get_goods_receipt_documents(req, __import__("uuid").UUID(it["id"]))
        jenis = [x["kind"] for g in b["groups"] if g["key"] != "rekap" for x in g["docs"]]
        harus(f"{it['receipt_number']}: documents daftar == bundel detail ({jenis})",
              [x["type"] for x in it["documents"]] == jenis and it["doc_count"] == len(jenis), [it["documents"], jenis])
    catat["contoh_daftar"] = {"total": d["total"], "items": [{k: it[k] for k in ("id", "receipt_number", "po_number", "status", "documents",
                                                                               "doc_count")} for it in items]}
    await pool.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
