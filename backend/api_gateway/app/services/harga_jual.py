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


def pilih_baris(cocok: list) -> dict:
    """Baris harga yang dipakai di antara yang sah+memenuhi qty: tingkat qty TERTINGGI dulu, pada kunci sama mulai TERBARU (<= tanggal dokumen, sudah
    disaring pemanggil; tanpa tanggal = paling lama). BUG-245: max() polos pada kunci sama jatuh ke baris pertama (tak terdefinisi)."""
    return max(cocok, key=lambda x: (Decimal(str(x["min_quantity"])), x["start_date"] or date.min))


def _berlaku(r, tanggal: date) -> bool:
    return (r["start_date"] is None or r["start_date"] <= tanggal) and (r["end_date"] is None or r["end_date"] >= tanggal)


STATUS_DAFTAR = {"aktif": "Aktif", "nonaktif": "Nonaktif", "kedaluwarsa": "Kedaluwarsa", "belum_berlaku": "Belum berlaku"}


def status_daftar(r, tanggal) -> dict:
    """Status berlaku daftar harga untuk tampilan (ANDROID U3b, 4 Nov 2026) -- aturan SAMA dengan penentu harga (_berlaku):
    nonaktif > kedaluwarsa (end_date < tanggal) > belum berlaku (start_date > tanggal) > aktif. tanggal = tanggal bisnis tenant."""
    if not r["is_active"]:
        k = "nonaktif"
    elif r["end_date"] is not None and r["end_date"] < tanggal:
        k = "kedaluwarsa"
    elif r["start_date"] is not None and r["start_date"] > tanggal:
        k = "belum_berlaku"
    else:
        k = "aktif"
    return {"status": k, "status_text": STATUS_DAFTAR[k], "is_expired": k == "kedaluwarsa"}


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


def _dari_daftar_dasar(baris: list, alias, dasar, kanon_baris, q: Decimal, h: dict, tanggal: date):
    """BUG-320 (putusan pemilik 20 Nov, pola SAP B1: harga DH satuan dasar x faktor): satuan baris bukan dasar dan DH TAK punya baris satuan itu -> pakai baris DH SATUAN DASAR
    (tingkat qty = q x faktor); harga per satuan baris = harga dasar-DH x faktor. None bila satuan = dasar, tanpa faktor/blok, atau tak ada baris dasar cocok."""
    if h["blok"] or not dasar or kanon_baris == SD.kanon(dasar, alias):
        return None
    q_dasar = q * h["faktor"]
    cocok = [r for r in baris if SD.kanon(r["unit"] or dasar, alias) == SD.kanon(dasar, alias)
             and _berlaku(r, tanggal) and Decimal(str(r["min_quantity"])) <= q_dasar]
    if not cocok:
        return None
    r = pilih_baris(cocok)
    return {"price": _r2(r["price"]), "min_quantity": r["min_quantity"], "min_price": r["min_price"], "discount_percent": r["discount_percent"]}  # BUG-339: ikut ke satuan turunan


PENANDA_BUG339 = "BUG-339 daftar_konversi: diskon% + minimum (dasar x faktor)"  # penanda verifikasi pasca-rilis (grep di kode berjalan); tak dipakai logika
SUMBER_TEKS = {"daftar": "Daftar harga", "daftar_konversi": "Daftar harga (dari satuan dasar)", "harga_satuan": "Harga satuan", "harga_dasar": "Harga dasar"}  # kosong -> None
KOLOM_HARGA = {"jual": "sales_price", "beli": "purchase_price"}


async def harga_saran(conn, tid: str, customer_id, tanggal: date, baris: list, arah: str = "jual") -> dict:
    """baris = [{key?, item_id, unit?, quantity}] -> {price_list, lines: [{key, item_id, unit, quantity, harga, sumber, daftar,
    tingkat_min_qty, satuan_dasar, faktor, blok}]}.
    arah "beli" (BUG-032-BE, putusan pemilik LANGSUNG 5 Nov 2026 "konversi lalu dasar x faktor"): tanpa daftar harga (daftar = penjualan
    per pelanggan); unit_conversions.purchase_price bila diisi -> products.purchase_price x faktor. Urutan & bentuk SAMA dengan jual."""
    kolom = KOLOM_HARGA[arah]
    daftar = await daftar_untuk(conn, tid, customer_id, tanggal) if arah == "jual" else None
    ids = list({str(b["item_id"]) for b in baris if b.get("item_id")})
    ktx = await SD.muat(conn, tid, ids)  # dasar + alias + konversi (sumber yang SAMA dengan jalur stok)
    alias = ktx["alias"]
    dasar_harga, harga_konv, baris_daftar = {}, {}, {}
    if ids:
        for r in await conn.fetch(f"SELECT id, {kolom} AS harga FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, ids):
            dasar_harga[str(r["id"])] = r["harga"]
        for r in await conn.fetch(
                f"""SELECT product_id, conversion_unit, {kolom} AS harga FROM unit_conversions WHERE tenant_id = $1
                    AND product_id = ANY($2::uuid[]) AND COALESCE(is_active, true) AND {kolom} IS NOT NULL""", tid, ids):
            harga_konv.setdefault(str(r["product_id"]), {}).setdefault(SD.kanon(r["conversion_unit"], alias), r["harga"])
        if daftar:
            for r in await conn.fetch(
                    """SELECT item_id, unit, price, min_price, discount_percent, min_quantity, start_date, end_date FROM price_list_items
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
                 "daftar": None, "tingkat_min_qty": None, "harga_minimum": None, "diskon_persen": None, "satuan_dasar": dasar, "faktor": float(h["faktor"]),
                 "blok": {"code": h["blok"]["code"], "message": h["blok"]["message"]} if h["blok"] else None}
        cocok = [r for r in baris_daftar.get(pid, []) if SD.kanon(r["unit"] or dasar, alias) == kanon_baris
                 and _berlaku(r, tanggal) and Decimal(str(r["min_quantity"])) <= q]
        if cocok:
            r = pilih_baris(cocok)
            hasil.update(harga=float(_r2(r["price"])), sumber="daftar", daftar={"id": str(daftar["id"]), "code": daftar["code"]},
                         tingkat_min_qty=float(r["min_quantity"]),
                         harga_minimum=float(_r2(r["min_price"])) if r["min_price"] is not None else None,
                         diskon_persen=float(r["discount_percent"]) if r["discount_percent"] is not None else None)  # BUG-253 diskon % baris yang menang (null = tak diatur, BUKAN 0)  # BUG-040 lantai baris yang menang (null = tak ada)
        elif (konv := _dari_daftar_dasar(baris_daftar.get(pid, []), alias, dasar, kanon_baris, q, h, tanggal)) is not None:
            hasil.update(harga=float(_r2(konv["price"] * h["faktor"])), sumber="daftar_konversi", daftar={"id": str(daftar["id"]), "code": daftar["code"]},
                         tingkat_min_qty=float(konv["min_quantity"]),  # BUG-320: DH dasar x faktor (tingkat qty di satuan dasar)
                         # BUG-339 (putusan pemilik LANGSUNG 22 Nov): diskon % baris dasar ikut (null = tak diatur, 0 eksplisit tetap 0); minimum satuan turunan = minimum dasar x faktor
                         harga_minimum=float(_r2(Decimal(str(konv["min_price"])) * h["faktor"])) if konv["min_price"] is not None else None,
                         diskon_persen=float(konv["discount_percent"]) if konv["discount_percent"] is not None else None)
        elif SD.normal(b.get("unit")) and kanon_baris != SD.kanon(dasar, alias) and kanon_baris in harga_konv.get(pid, {}):
            hasil.update(harga=float(_r2(harga_konv[pid][kanon_baris])), sumber="harga_satuan")
        elif not h["blok"] and dasar_harga.get(pid) is not None:
            hasil.update(harga=float(_r2(Decimal(str(dasar_harga[pid])) * h["faktor"])), sumber="harga_dasar")
        hasil["sumber_text"] = SUMBER_TEKS.get(hasil["sumber"])
        out.append(hasil)
    return {"price_list": ({"id": str(daftar["id"]), "code": daftar["code"], "name": daftar["name"], "asal": daftar["asal"]}
                           if daftar else None), "lines": out}
