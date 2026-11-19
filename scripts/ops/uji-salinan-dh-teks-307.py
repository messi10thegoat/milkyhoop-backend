"""BUG-307 uji DB di SALINAN (bukan prod): handler ASLI GET /price-lists/{id}, tenant kaos + nyawit. Hanya BACA.
A tiap baris harga: min_price_text == None bila min_price NULL, selain itu rupiah dari SQL independen; diskon_text == None bila NULL, selain itu f"{v:g}%" (0 -> '0%')
B jumlah baris respons == jumlah baris SQL (tak ada yang hilang)"""
import asyncio
import sys
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.routers import price_lists as PL  # noqa: E402

TENANT = ("pt-kita-nyawit-bersama", "kaos-biru-konveksi")
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan", "role": "OWNER"}
    return r


def rp(v):
    d = Decimal(str(v))
    s = f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}"
    return "Rp " + s.replace(",", "X").replace(".", ",").replace("X", ".")


async def main():
    conn = await IT.get_db_connection()
    for tid in TENANT:
        daftar = await conn.fetch("SELECT id FROM price_lists WHERE tenant_id = $1 ORDER BY created_at LIMIT 8", tid)
        n_baris = n_min = n_dis = 0
        for d in daftar:
            data = (await PL.get_price_list(req(tid), d["id"]))["data"]
            sql = {str(r["id"]): r for r in await conn.fetch(
                "SELECT pli.id, pli.min_price, pli.discount_percent FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id "
                "WHERE pl.tenant_id = $1 AND pli.price_list_id = $2", tid, d["id"])}
            cek(f"B {tid} {str(d['id'])[:8]}: jumlah baris", len(data["items"]) == len(sql), f"{len(data['items'])} vs {len(sql)}")
            for it in data["items"]:
                r = sql[it["id"]]
                n_baris += 1
                e_min = None if r["min_price"] is None else rp(r["min_price"])
                e_dis = None if r["discount_percent"] is None else f"{float(r['discount_percent']):g}%"
                n_min += e_min is not None
                n_dis += e_dis is not None
                if it["min_price_text"] != e_min or it["diskon_text"] != e_dis:
                    cek(f"A {tid} baris {it['id'][:8]}", False, f"{it['min_price_text']!r}/{it['diskon_text']!r} vs {e_min!r}/{e_dis!r}")
        print(f"   {tid}: {n_baris} baris, {n_min} ber-minimum, {n_dis} ber-diskon")
        cek(f"A {tid}: ada baris diperiksa", n_baris > 0, str(n_baris))
    ok = all(HASIL) and len(HASIL) > 0
    print(f"{sum(HASIL)}/{len(HASIL)} lulus")
    sys.exit(0 if ok else 1)

asyncio.run(main())
