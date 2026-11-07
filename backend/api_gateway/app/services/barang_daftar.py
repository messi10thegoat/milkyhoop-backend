"""Daftar BARANG & JASA standar SO (kontrak + putusan MASTER 7 Nov 2026; pola NetSuite Items / QBO Low stock-Out of stock): SATU penentu
kunci aksi, teks posisi, dan aksi tersedia untuk GET /api/items (?aksi=, ?stock_status=, baris), /items/summary ({k}_count/_value),
/items/stats, dan detail. Dulu TIGA definisi "habis": daftar stock_status (tanpa syarat aktif), /summary (tanpa syarat aktif), /stats
(aktif) -> nyawit 7 Nov: kartu 11 vs daftar 19 (8 barang nonaktif ikut). Kini satu: barang DILACAK = goods ∧ track_inventory ∧ aktif.

Kunci (stok dari inventory_ledger, Law 1/16 versi kuantitas; `stok` = ekspresi SQL stok per baris):
  stok_habis            = dilacak ∧ stok <= 0 (stok MINUS ikut; pembeda di position_text)          PILL, desak 1
  di_bawah_titik_pesan  = dilacak ∧ reorder_level > 0 ∧ 0 < stok <= reorder_level                 PILL, desak 2 (partisi dgn habis)
  tanpa_harga_jual      = aktif ∧ for_sales ∧ harga jual 0 (BUKAN partisi stok: barang bisa di dua pill) PILL, desak 3
  nonaktif              = status <> active                                                         SARING (bukan pill)
Barang tak dilacak (non_inventory / goods tanpa lacak / jasa) TAK PERNAH masuk pill stok (mayoritas grapgrap)."""
from decimal import Decimal
from typing import Optional

AKSI_BARANG = ("stok_habis", "di_bawah_titik_pesan", "tanpa_harga_jual")  # urut desak; pill hitung
SARING_BARANG = ("nonaktif",)
SEMUA_KUNCI = AKSI_BARANG + SARING_BARANG
STOK_SQL = "COALESCE(per.jumlah, 0)"
# stok + nilai per barang dari inventory_ledger (alias `per`), dipakai /summary dan /stats; daftar memakai versi + transaction_count
SQL_PER_STOK = """LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) as jumlah,
        COALESCE(SUM(il.total_cost * SIGN(il.quantity_in - il.quantity_out)), 0) as total_nilai
        FROM inventory_ledger il
        WHERE il.product_id = p.id AND il.tenant_id = CAST(p.tenant_id AS TEXT)
    ) per ON true"""
# nilai persediaan = semua barang ber-lacak stok (termasuk nonaktif: stoknya tetap aset) -- BUG-034, sama di daftar/summary/stats
SQL_NILAI_STOK = "COALESCE(SUM(per.total_nilai) FILTER (WHERE p.item_type = 'goods' AND p.track_inventory = true), 0)"


def sql_dilacak(a: str = "p") -> str:
    return f"({a}.item_type = 'goods' AND {a}.track_inventory = true AND {a}.status = 'active')"


def sql_harga_jual_nol(a: str = "p") -> str:
    return f"GREATEST(COALESCE({a}.sales_price, 0), COALESCE({a}.harga_jual, 0)) <= 0"


def sql_aksi(kunci: str, stok: str = STOK_SQL, a: str = "p") -> str:
    return {
        "stok_habis": f"({sql_dilacak(a)} AND {stok} <= 0)",
        "di_bawah_titik_pesan": f"({sql_dilacak(a)} AND COALESCE({a}.reorder_level, 0) > 0 AND {stok} > 0 AND {stok} <= {a}.reorder_level)",
        "tanpa_harga_jual": f"({a}.status = 'active' AND COALESCE({a}.for_sales, true) AND {sql_harga_jual_nol(a)})",
        "nonaktif": f"({a}.status <> 'active')",
    }[kunci]


def sql_di_bawah_reorder(stok: str = STOK_SQL, a: str = "p") -> str:
    """Kartu STOK RENDAH /stats belowReorder (BUG-092): dilacak ∧ ber-titik pesan ∧ stok <= titik (habis ikut) = habis∪di_bawah."""
    return f"({sql_dilacak(a)} AND {a}.reorder_level > 0 AND {stok} <= {a}.reorder_level)"


def sql_ada_stok(stok: str = STOK_SQL, a: str = "p") -> str:
    """stock_status=in_stock (lama): dilacak ∧ stok di atas titik pesan."""
    return f"({sql_dilacak(a)} AND {stok} > COALESCE({a}.reorder_level, 0))"


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def dilacak(b: dict) -> bool:
    return b.get("item_type", "goods") == "goods" and bool(b.get("track_inventory")) and b.get("status", "active") == "active"


def aksi_semua(b: dict, stok) -> list:
    """Setara sql_aksi (uji berpasangan). b = medan barang (item_type, track_inventory, status, reorder_level, for_sales, sales_price, harga_jual)."""
    hasil = []
    s, titik = _d(stok), _d(b.get("reorder_level"))
    if dilacak(b):
        if s <= 0:
            hasil.append("stok_habis")
        elif titik > 0 and s <= titik:
            hasil.append("di_bawah_titik_pesan")
    if b.get("status", "active") == "active" and b.get("for_sales", True) is not False \
            and max(_d(b.get("sales_price")), _d(b.get("harga_jual"))) <= 0:
        hasil.append("tanpa_harga_jual")
    return hasil


def _q(x: Decimal) -> str:
    """Angka stok ringkas Indonesia: 172 · 1,5 · 1.000 (tanpa nol ekor)."""
    bulat, _, pecah = f"{x.normalize():f}".partition(".")
    neg = bulat.startswith("-")
    bulat = bulat.lstrip("-")
    bulat = f"{int(bulat):,}".replace(",", ".")
    return ("-" if neg else "") + bulat + (f",{pecah.rstrip('0')}" if pecah.rstrip("0") else "")


def position_text(b: dict, stok, bisa_dijual=None, satuan: Optional[str] = None) -> str:
    """Teks posisi SERVER: "Stok 172 pcs · bisa dijual 160" · "Stok habis" · "Stok minus 3 pcs · perlu hitung fisik" · "Tanpa stok" ·
    "Jasa" · "Nonaktif"."""
    if b.get("status", "active") != "active":
        return "Nonaktif"
    if b.get("item_type") == "service":
        return "Jasa"
    if not dilacak(b):
        return "Tanpa stok"
    s, sat = _d(stok), (f" {satuan}" if satuan else "")
    if s < 0:
        return f"Stok minus {_q(-s)}{sat} · perlu hitung fisik"
    if s == 0:
        return "Stok habis"
    teks = f"Stok {_q(s)}{sat}"
    if bisa_dijual is not None and _d(bisa_dijual) != s:
        teks += f" · bisa dijual {_q(_d(bisa_dijual))}"
    return teks


def aksi_tersedia(b: dict) -> dict:
    """Detail: {aksi: {tersedia, alasan}} -- alasan kalimat bila tak tersedia."""
    aktif = b.get("status", "active") == "active"
    lacak = b.get("item_type", "goods") == "goods" and bool(b.get("track_inventory"))
    return {
        "nonaktifkan": {"tersedia": aktif, "alasan": None if aktif else "Barang sudah nonaktif."},
        "aktifkan": {"tersedia": not aktif, "alasan": None if not aktif else "Barang sudah aktif."},
        "sesuaikan_stok": {"tersedia": aktif and lacak, "alasan": None if (aktif and lacak) else (
            "Barang nonaktif; aktifkan dulu." if not aktif else "Stok barang ini tidak dilacak.")},
    }
