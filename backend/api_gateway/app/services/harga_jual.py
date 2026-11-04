"""DAFTAR-HARGA (4 Nov 2026, tahap A-C; desain + putusan MASTER): SATU penentu harga jual saran per baris dokumen penjualan.

Pola Accurate "Kategori Pelanggan -> Tingkat Harga" / NetSuite price level. Urutan (putusan MASTER):
  1. daftar harga pelanggan (customers.price_list_id; NULL / nonaktif / di luar masa berlaku -> daftar BAWAAN tenant):
     baris barang x SATUAN yang sama (alias kamus satuan_dasar: "Kg" == "kilogram"; unit kosong = satuan dasar), aktif dan berlaku
     pada tanggal dokumen, tingkat qty TERTINGGI dengan min_quantity <= qty (qty dalam satuan baris itu);
  2. harga per satuan dari konversi barang (unit_conversions.sales_price) bila satuan bukan dasar;
  3. harga dasar barang (products.sales_price = "Harga jual" form barang; harga_jual hanya salinannya) x faktor konversi.
Barang tanpa harga dasar -> harga None, sumber "kosong" (isian manual, bukan blok). Satuan tanpa konversi -> blok berkalimat
SATUAN_TAK_TERKONVERSI (penentu satuan_dasar yang SAMA dengan jalur stok). Uang Decimal 2dp HALF_UP (Law 9/25). Hanya MEMBACA."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from . import satuan_dasar as SD

SEN = Decimal("0.01")


def _r2(x) -> Decimal:
    return Decimal(str(x)).quantize(SEN, rounding=ROUND_HALF_UP)


def _berlaku(r, tanggal: date) -> bool:
    return (r["start_date"] is None or r["start_date"] <= tanggal) and (r["end_date"] is None or r["end_date"] >= tanggal)


async def daftar_untuk(conn, tid: str, customer_id, tanggal: date) -> Optional[dict]:
    """Daftar yang berlaku: milik pelanggan (aktif + berlaku) -> asal 'pelanggan'; selain itu daftar bawaan tenant -> 'bawaan'; None."""
    q = "SELECT id, code, name, is_active, start_date, end_date FROM price_lists WHERE tenant_id = $1 AND "
    if customer_id:
        pl = await conn.fetchval("SELECT price_list_id FROM customers WHERE id = $1 AND tenant_id = $2", customer_id, tid)
        if pl:
            r = await conn.fetchrow(q + "id = $2", tid, pl)
            if r and r["is_active"] and _berlaku(r, tanggal):
                return {"id": r["id"], "code": r["code"], "name": r["name"], "asal": "pelanggan"}
    r = await conn.fetchrow(q + "is_default AND is_active", tid)
    if r and _berlaku(r, tanggal):
        return {"id": r["id"], "code": r["code"], "name": r["name"], "asal": "bawaan"}
    return None


async def harga_saran(conn, tid: str, customer_id, tanggal: date, baris: list) -> dict:
    """baris = [{key?, item_id, unit?, quantity}] -> {price_list, lines: [{key, item_id, unit, quantity, harga, sumber, daftar,
    tingkat_min_qty, satuan_dasar, faktor, blok}]}."""
    daftar = await daftar_untuk(conn, tid, customer_id, tanggal)
    ids = list({str(b["item_id"]) for b in baris if b.get("item_id")})
    ktx = await SD.muat(conn, tid, ids)  # dasar + alias + konversi (sumber yang SAMA dengan jalur stok)
    alias = ktx["alias"]
    dasar_harga, harga_konv, baris_daftar = {}, {}, {}
    if ids:
        for r in await conn.fetch("SELECT id, sales_price FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids):
            dasar_harga[str(r["id"])] = r["sales_price"]
        for r in await conn.fetch(
                """SELECT product_id, conversion_unit, sales_price FROM unit_conversions WHERE tenant_id = $1 AND product_id = ANY($2::uuid[])
                   AND COALESCE(is_active, true) AND sales_price IS NOT NULL""", tid, ids):
            harga_konv.setdefault(str(r["product_id"]), {}).setdefault(SD.kanon(r["conversion_unit"], alias), r["sales_price"])
        if daftar:
            for r in await conn.fetch(
                    """SELECT item_id, unit, price, min_quantity, start_date, end_date FROM price_list_items
                       WHERE price_list_id = $1 AND item_id = ANY($2::uuid[]) AND COALESCE(is_active, true)""", daftar["id"], ids):
                baris_daftar.setdefault(str(r["item_id"]), []).append(r)
    out = []
    for b in baris:
        pid = str(b["item_id"]) if b.get("item_id") else None
        q = Decimal(str(b.get("quantity") or 0))
        brg = ktx["barang"].get(pid) if pid else None
        dasar = brg["dasar"] if brg else None
        kanon_baris = SD.kanon(b.get("unit") or dasar, alias)
        h = SD.hitung(ktx, pid, b.get("unit"), q, "konversi")
        hasil = {"key": b.get("key"), "item_id": pid, "unit": b.get("unit"), "quantity": float(q), "harga": None, "sumber": "kosong",
                 "daftar": None, "tingkat_min_qty": None, "satuan_dasar": dasar, "faktor": float(h["faktor"]),
                 "blok": {"code": h["blok"]["code"], "message": h["blok"]["message"]} if h["blok"] else None}
        cocok = [r for r in baris_daftar.get(pid, []) if SD.kanon(r["unit"] or dasar, alias) == kanon_baris
                 and _berlaku(r, tanggal) and Decimal(str(r["min_quantity"])) <= q]
        if cocok:
            r = max(cocok, key=lambda x: Decimal(str(x["min_quantity"])))
            hasil.update(harga=float(_r2(r["price"])), sumber="daftar", daftar={"id": str(daftar["id"]), "code": daftar["code"]},
                         tingkat_min_qty=float(r["min_quantity"]))
        elif SD.normal(b.get("unit")) and kanon_baris != SD.kanon(dasar, alias) and kanon_baris in harga_konv.get(pid, {}):
            hasil.update(harga=float(_r2(harga_konv[pid][kanon_baris])), sumber="harga_satuan")
        elif not h["blok"] and dasar_harga.get(pid) is not None:
            hasil.update(harga=float(_r2(Decimal(str(dasar_harga[pid])) * h["faktor"])), sumber="harga_dasar")
        out.append(hasil)
    return {"price_list": ({"id": str(daftar["id"]), "code": daftar["code"], "name": daftar["name"], "asal": daftar["asal"]}
                           if daftar else None), "lines": out}
