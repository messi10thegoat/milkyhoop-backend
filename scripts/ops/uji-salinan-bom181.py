"""BUG-181 uji di SALINAN (bukan prod): GET /api/items/autocomplete (handler ASLI) atas klon, tenant pt-busana-indah-semesta (+ kaos kontrol lintas tenant). Hanya BACA.
H1 hasil LAMA (predikat lama ILIKE %q% di nama/kode/sku, dihitung SQL mandiri) ⊆ hasil BARU untuk 12 kata kunci × {semua, goods} · H2 "Polos Kaos" → BRG-0001; "Kaos  Polos" (spasi ganda) → BRG-0001
· H3 % dan _ literal: q="%" dan q="_" → 0 hasil (dulu semua barang) · H4 hasil baru hanya milik tenant sendiri · H5 daftar barang ?search= (fungsi SAMA): "Polos Kaos" menemukan BRG-0001."""
import asyncio
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402

BS, KA = "pt-busana-indah-semesta", "kaos-biru-konveksi"
KUNCI = ("Kaos Polos", "kaos polos", "Kaos  Polos", "BHN-KAIN", "bhn-kain", "BRG", "Kain", "Kain Cotton", "Polos Kaos", "Cotton 30s", "BRG-0001", "BHN")
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def lama(c, tid, q, tipe):
    return {str(r["id"]) for r in await c.fetch(
        """SELECT p.id FROM products p WHERE p.tenant_id = $1 AND (p.nama_produk ILIKE $2 OR p.item_code ILIKE $2 OR p.sku ILIKE $2)
           AND p.status = 'active' AND p.deleted_at IS NULL AND ($3::text IS NULL OR p.item_type = $3)""", tid, f"%{q}%", tipe)}


async def baru(c, tid, q, tipe):
    out = await IT.autocomplete_items(await req(c, tid), q, 50, tipe)
    return [(x["id"], x["code"]) for x in (out.get("items") or out.get("data") or [])]


async def main():
    pool = await IT.get_pool()
    async with pool.acquire() as c:
        for tipe in (None, "goods"):
            for q in KUNCI:
                b = await baru(c, BS, q, tipe)
                l = await lama(c, BS, q, tipe)
                cek(f"H1 q={q!r} tipe={tipe}: lama ⊆ baru ({len(l)} ⊆ {len(b)})", l <= {i for i, _ in b}, f"{l} vs {b}")
        for q in ("Polos Kaos", "Kaos  Polos", "  kaos   polos  "):
            b = await baru(c, BS, q, None)
            cek(f"H2 q={q!r} -> BRG-0001", ("BRG-0001" in [k for _, k in b]) and len(b) == 1, str(b))
        for q in ("%", "_", "\\", "Kaos%"):
            b = await baru(c, BS, q, None)
            cek(f"H3 q={q!r} literal: 0 hasil", b == [], str(b))
        semua = await baru(c, BS, "", None)
        kaos = await baru(c, KA, "", None)
        milik = {str(r["id"]) for r in await c.fetch("SELECT id FROM products WHERE tenant_id = $1", BS)}
        cek("H4 tanpa kata: hanya barang tenant sendiri, bukan tenant lain", {i for i, _ in semua} <= milik and not ({i for i, _ in semua} & {i for i, _ in kaos}), f"{len(semua)} {len(kaos)}")
        cond, par = IT._syarat_cari("Polos Kaos", 2)
        n = await c.fetchval(f"SELECT count(*) FROM products p WHERE p.tenant_id = $1 {cond}", BS, *par)
        cek("H5 fungsi daftar barang (_syarat_cari) 'Polos Kaos' -> 1", n == 1, str(n))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
