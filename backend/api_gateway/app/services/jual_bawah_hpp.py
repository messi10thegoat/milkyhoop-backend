"""BUG-207 (putusan pemilik 13 Nov): PERINGATAN konfirmasi (bukan blok) -- SATU penentu dipakai Faktur/SO/Penawaran/Proforma (jual di bawah HPP)
dan penerimaan barang (biaya per satuan janggal, salah konversi). Dihitung server; tanpa migrasi, tanpa jurnal, tak ada yang disimpan.
Angka HPP/rugi HANYA bagi pengguna yang boleh membaca Laporan (can(REPORT,'R'), putusan pemilik 13 Nov ~21:15): yang lain mendapat pesan tanpa angka
dan kunci angka ABSEN dari JSON (bukan null).
Alur: `hitung_*` -> daftar MENTAH (dengan angka) -> `sajikan(mentah, boleh_hpp)` -> objek JSON final. Penentu tak pernah mengembalikan angka ke luar tanpa lewat sajikan."""
import logging
from decimal import Decimal
from typing import Iterable, List, Optional

from . import satuan_dasar as SD
from . import teks_galat as tg

logger = logging.getLogger(__name__)
NOL = Decimal(0)
KODE_JUAL = "JUAL_DI_BAWAH_HPP"
KODE_TERIMA = "BIAYA_TERIMA_JANGGAL"
AMBANG_SELISIH = Decimal("0.30")  # biaya terima menyimpang LEBIH dari 30% dari HPP (dua arah); konstanta (putusan 13 Nov)
# Penentu izin = penentu rute YANG SAMA dgn middleware: jalur baca Laporan -> (modul reports, huruf R) -> can(REPORT, R).
JALUR_IZIN_HPP = "/api/reports/profit-loss"

SQL_HPP = """
SELECT p.id, p.nama_produk AS nama,
       COALESCE(NULLIF((SELECT il.average_cost FROM inventory_ledger il WHERE il.tenant_id = p.tenant_id AND il.product_id = p.id
                        ORDER BY il.created_at DESC LIMIT 1), 0), NULLIF(p.purchase_price, 0), 0) AS hpp
FROM products p
WHERE p.tenant_id = $1 AND p.id = ANY($2::uuid[]) AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true)"""


async def boleh_lihat_hpp(user: Optional[dict]) -> bool:
    """can(REPORT,'R') lewat penentu yang sama dgn middleware (izin_rute_pengguna). Tanpa pengguna / gagal baca = TIDAK (aman)."""
    if not user:
        return False
    from ..middleware.permission_middleware import izin_rute_pengguna
    return bool((await izin_rute_pengguna(user, [JALUR_IZIN_HPP], "GET")).get(JALUR_IZIN_HPP))


def _rp(v: Decimal) -> str:
    q = v.quantize(Decimal("1")) if v >= 100 else v.quantize(Decimal("0.01"))
    if q == q.to_integral_value():
        return tg.rp(int(q))
    return "Rp " + f"{q:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _angka(v: Decimal) -> str:
    return format(v.quantize(Decimal("0.01")), "f")


async def _hpp_barang(conn, tid: str, ids: Iterable) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    return {str(r["id"]): {"nama": r["nama"], "hpp": Decimal(str(r["hpp"] or 0))} for r in await conn.fetch(SQL_HPP, tid, ids)}


async def hitung_jual(conn, tid: str, lines: Iterable[dict]) -> List[dict]:
    """lines = [{item_id, quantity, unit, nilai}], nilai = harga bersih baris SEBELUM PPN sesudah diskon baris + diskon dokumen (compute_document
    `dpp_harga_jual`). Hanya barang goods ber-lacak-stok dengan HPP > 0 (HPP 0/tak diketahui = tak menebak, tanpa peringatan). Harga per SATUAN DASAR
    (nilai / qty dasar) < HPP -> peringatan MENTAH. Harga 0 (hadiah) tetap diberi peringatan."""
    lines = [x for x in lines if x.get("item_id")]
    barang = await _hpp_barang(conn, tid, [x["item_id"] for x in lines])
    if not barang:
        return []
    ktx = await SD.muat(conn, tid, [x["item_id"] for x in lines if str(x["item_id"]) in barang])
    out = []
    for n, x in enumerate(lines, 1):
        n = x.get("baris", n)
        i = str(x["item_id"])
        b = barang.get(i)
        if b is None or b["hpp"] <= NOL:
            continue
        h = SD.hitung(ktx, i, x.get("unit"), x.get("quantity"), "konversi")
        if h["blok"] or h["qty_dasar"] <= NOL:
            continue  # satuan tak terkonversi / qty 0: bukan urusan peringatan (penjaga satuan dokumen yang menolak)
        harga = SD.biaya_dasar(x.get("nilai"), h["qty_dasar"])
        if harga < b["hpp"]:
            out.append({"kode": KODE_JUAL, "baris": n, "product_id": i, "nama": b["nama"], "satuan": h["satuan_dasar"],
                        "harga": harga, "hpp": b["hpp"], "selisih": (b["hpp"] - harga) / b["hpp"] * 100})
    return out


async def hitung_terima(conn, tid: str, lines: Iterable[dict]) -> List[dict]:
    """lines = [{item_id, quantity, unit, nilai}], nilai = subtotal baris PO (sesudah diskon baris, sebelum PPN) untuk qty `quantity` (yang DITERIMA ini).
    Biaya per satuan dasar menyimpang LEBIH dari 30% dari HPP saat ini (murah ATAU mahal) -> peringatan MENTAH. HPP tak diketahui = tanpa peringatan."""
    lines = [x for x in lines if x.get("item_id")]
    barang = await _hpp_barang(conn, tid, [x["item_id"] for x in lines])
    if not barang:
        return []
    ktx = await SD.muat(conn, tid, [x["item_id"] for x in lines if str(x["item_id"]) in barang])
    out = []
    for n, x in enumerate(lines, 1):
        n = x.get("baris", n)
        i = str(x["item_id"])
        b = barang.get(i)
        if b is None or b["hpp"] <= NOL:
            continue
        h = SD.hitung(ktx, i, x.get("unit"), x.get("quantity"), "konversi")
        if h["blok"] or h["qty_dasar"] <= NOL:
            continue
        biaya = SD.biaya_dasar(x.get("nilai"), h["qty_dasar"])
        selisih = abs(biaya - b["hpp"]) / b["hpp"]
        if selisih > AMBANG_SELISIH:
            out.append({"kode": KODE_TERIMA, "baris": n, "product_id": i, "nama": b["nama"], "satuan": h["satuan_dasar"],
                        "harga": biaya, "hpp": b["hpp"], "selisih": selisih * 100, "murah": biaya < b["hpp"]})
    return out


def sajikan(mentah: Iterable[dict], boleh_hpp: bool) -> List[dict]:
    """Daftar MENTAH -> objek JSON final. boleh_hpp=False: pesan TANPA angka dan kunci angka (harga_jual/biaya, hpp, selisih_pct) ABSEN."""
    hasil = []
    for m in mentah:
        jual = m["kode"] == KODE_JUAL
        o = {"code": m["kode"], "butuh_konfirmasi": True, "baris": m["baris"], "product_id": m["product_id"], "nama": m["nama"]}
        if boleh_hpp:
            if jual:
                o["pesan"] = (f"Harga jual {m['nama']} {_rp(m['harga'])} per {m['satuan']} di bawah harga pokok {_rp(m['hpp'])} "
                              f"(selisih {_angka(m['selisih'])}%).")
                o["harga_jual"] = _angka(m["harga"])
            else:
                o["pesan"] = (f"Biaya {m['nama']} {_rp(m['harga'])} per {m['satuan']} menyimpang {_angka(m['selisih'])}% "
                              f"dari harga pokok {_rp(m['hpp'])}; periksa satuan dan konversi.")
                o["biaya"] = _angka(m["harga"])
            o["hpp"] = _angka(m["hpp"])
            o["selisih_pct"] = _angka(m["selisih"])
        else:
            o["pesan"] = (f"Harga jual {m['nama']} di bawah harga pokok barang." if jual
                          else f"Biaya {m['nama']} menyimpang jauh dari harga pokok barang; periksa satuan dan konversi.")
        hasil.append(o)
    return hasil


async def peringatan_jual(conn, tid: str, user: Optional[dict], lines: Iterable[dict]) -> List[dict]:
    """Jalan pintas untuk router: hitung + izin + sajikan (satu panggilan). Peringatan = ISYARAT: gagal hitung tak boleh merusak kalkulasi utama
    (dicatat, daftar kosong)."""
    try:
        return sajikan(await hitung_jual(conn, tid, lines), await boleh_lihat_hpp(user))
    except Exception:  # noqa: BLE001
        logger.error("BUG-207 peringatan_jual gagal", exc_info=True)
        return []


async def peringatan_terima(conn, tid: str, user: Optional[dict], rencana: dict) -> List[dict]:
    """Dari hasil po_rencana.rencana_terima: baris barang yang AKAN diterima (qty akan_diterima), nilai = subtotal baris x akan/qty pesanan."""
    try:
        lines = []
        for n, r in enumerate(rencana.get("semua") or [], 1):
            akan = Decimal(str((rencana.get("akan") or {}).get(str(r["id"]), 0) or 0))
            q = Decimal(str(r["quantity"] or 0))
            if r.get("jasa") or akan <= NOL or q <= NOL or not r.get("item_id"):
                continue
            lines.append({"baris": n, "item_id": r["item_id"], "quantity": akan, "unit": r.get("unit"),
                          "nilai": Decimal(str(r.get("subtotal") or 0)) * akan / q})
        return sajikan(await hitung_terima(conn, tid, lines), await boleh_lihat_hpp(user))
    except Exception:  # noqa: BLE001
        logger.error("BUG-207 peringatan_terima gagal", exc_info=True)
        return []


def baris_dari_hasil(items: Iterable[dict]) -> List[dict]:
    """Baris hasil compute_document -> masukan hitung_jual (nilai = dpp_harga_jual)."""
    return [{"item_id": ln.get("item_id"), "quantity": ln.get("quantity"), "unit": ln.get("unit"), "nilai": ln.get("dpp_harga_jual")} for ln in items]
