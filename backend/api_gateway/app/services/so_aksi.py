"""Pill AKSI daftar Pesanan Penjualan (W-20261014-AKSI-SO, 14 Okt 2026; pola quote 864f5ed2, putusan MASTER B1).

Kunci (pola NetSuite Pending Approval / Pending Fulfillment / Pending Billing), urut PALING MENDESAK dulu:
  telat_kirim        = berjalan, expected_ship_date < hari ini (tanggal bisnis tenant) DAN masih ada sisa kirim
  perlu_minta_dp     = (PERLU-MINTA-DP) berjalan, bersyarat DP, BELUM ditagih: tanpa proforma terbit apa pun, tanpa uang muka
                       hidup, tanpa faktur terbit, sisa > 0 (so_posisi.perlu_minta_dp; pola SAP down payment request)
  menunggu_dp        = (AKSI-SO-2) berjalan, fakta Posisi "Menunggu DP" tanpa fase lokasi -- SATU fungsi murni
                       so_posisi.menunggu_dp (dipanggil juga posisi()); pola NetSuite Pending Deposit
  perlu_dikonfirmasi = draft
  perlu_dikirim      = berjalan, ada baris PERLU DIKIRIM (COALESCE(perlu_kirim, track_inventory, false) -- ekspresi
                       sama dengan so_kirim.ringkasan_menunggu_kirim / V318) yang qty-nya belum habis dikirim SJ aktif,
                       DAN BUKAN fase lokasi 'tersedia' (eksklusif dengan siap_diserahkan; kartu tak dobel)
  siap_diserahkan    = (AKSI-SO-2) berjalan, sisa kirim seperti di atas DAN fase_lokasi = 'tersedia' (predikat SAMA dengan
                       so_agregat.PREDIKAT_TERSEDIA; lokasi = pengaturan per SO/tenant). Pola NetSuite Packed / Shopify
                       Ready for pickup. telat_kirim tak terpengaruh fase.
  perlu_ditagih      = berjalan, ada baris dengan qty BISA DITAGIH > qty difaktur terbit. B1: barang berSTOK
                       (track_inventory) bisa ditagih = terkirim; selain itu = qty pesan (grapgrap: tanda perlu_kirim =
                       flag serah, bukan syarat tagih; "tagih sesudah kirim" per tenant = tiket, bukan kode).
Berjalan = selain so_agregat.AKTIF_TIDAK (draft/cancelled/completed); arsip (cancelled/completed) tak masuk aksi mana pun.
Satu SO boleh masuk lebih dari satu kunci; kolom `aksi` per baris daftar = kunci pertama menurut urutan AKSI.
Terkirim = so_kirim.terkirim_per_baris (SJ aktif; quantity_shipped MATI). Difaktur = faktur TERBIT per baris
(sj_so.PRED_FAKTUR_TERBIT -- satu predikat dengan SQL_FAKTUR_TERBIT_PER_BARIS); kolom quantity_invoiced TIDAK dipakai
(menyimpang dari faktur, tiket). DP/proforma tak memengaruhi qty ditagih (bukan baris faktur).
Filter ?aksi=, kolom aksi per baris, dan /summary <kunci>_count/_value memanggil `fakta` yang SAMA; kartu dashboard
"DP belum diterima" (?tugas=dp_belum_diterima) = dp_belum_diterima() = menunggu_dp U perlu_minta_dp dari fakta yang SAMA.
_value = Σ total_amount SO, KECUALI kunci DP: menunggu_dp = sisa proforma DP belum terbayar, perlu_minta_dp = DP syarat SO.
"""
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from . import so_kirim, so_posisi
from .sj_so import PRED_FAKTUR_TERBIT
from .so_agregat import AKTIF_TIDAK

NOL = Decimal("0")
AKSI = ("telat_kirim", "perlu_minta_dp", "menunggu_dp", "perlu_dikonfirmasi", "perlu_dikirim", "siap_diserahkan",
        "perlu_ditagih")
KUNCI_DP = ("perlu_minta_dp", "menunggu_dp")  # saling lepas; kartu dashboard "DP belum diterima" = gabungannya
FASE_SIAP = "tersedia"  # = so_agregat.PREDIKAT_TERSEDIA (fase_lokasi = 'tersedia')
STATUS_DRAF = "draft"

SQL_FAKTUR_TERBIT_PER_BARIS_SO = f"""
    SELECT sii.sales_order_item_id AS soi_id, SUM(sii.quantity) AS q
    FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[]) AND {PRED_FAKTUR_TERBIT}
      AND sii.sales_order_item_id IS NOT NULL
    GROUP BY 1
"""
SQL_BARIS = """
    SELECT soi.id, soi.sales_order_id AS so_id, soi.quantity,
           COALESCE(soi.perlu_kirim, p.track_inventory, false) AS perlu, COALESCE(p.track_inventory, false) AS stok
    FROM sales_orders so
    JOIN sales_order_items soi ON soi.sales_order_id = so.id
    LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
    WHERE so.tenant_id = $1 AND so.id = ANY($2::uuid[])
"""


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def aksi_so(status: str, expected_ship_date: Optional[date], hari_ini: date, baris: Iterable[dict],
            dp: Optional[str] = None, fase_lokasi: Optional[str] = None) -> list:
    """Murni. baris: {quantity, perlu, stok, terkirim, difaktur}; dp = kunci KUNCI_DP dari so_posisi.fakta_dp (atau None).
    -> kunci AKSI yang berlaku, urut AKSI."""
    if status == STATUS_DRAF:
        return ["perlu_dikonfirmasi"]
    if status in AKTIF_TIDAK:
        return []
    sisa_kirim = sisa_tagih = False
    for b in baris:
        q, kirim, fak = _d(b["quantity"]), _d(b["terkirim"]), _d(b["difaktur"])
        if b["perlu"] and q - kirim > NOL:
            sisa_kirim = True
        if (kirim if b["stok"] else q) - fak > NOL:
            sisa_tagih = True
    out = []
    if sisa_kirim and expected_ship_date is not None and expected_ship_date < hari_ini:
        out.append("telat_kirim")
    if dp in KUNCI_DP:
        out.append(dp)
    if sisa_kirim:
        out.append("siap_diserahkan" if fase_lokasi == FASE_SIAP else "perlu_dikirim")
    if sisa_tagih:
        out.append("perlu_ditagih")
    return out


def aksi_utama(daftar: list) -> Optional[str]:
    return daftar[0] if daftar else None


async def fakta(conn, tenant_id: str, hari_ini: date, so_ids: Optional[list] = None) -> dict:
    """-> {so_id: {"aksi": [...], "total": Decimal}} untuk SEMUA SO tenant (so_ids None) atau so_ids saja."""
    if so_ids is None:
        so = await conn.fetch(
            "SELECT id, order_number, status, expected_ship_date, total_amount, fase_lokasi, dp_percent, dp_amount "
            "FROM sales_orders WHERE tenant_id = $1", tenant_id)
    else:
        if not so_ids:
            return {}
        so = await conn.fetch(
            "SELECT id, order_number, status, expected_ship_date, total_amount, fase_lokasi, dp_percent, dp_amount "
            "FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
            tenant_id, list(so_ids))
    jalan = sorted(r["id"] for r in so if r["status"] not in AKTIF_TIDAK)
    per_so: dict = {}
    dp: dict = {}
    if jalan:
        dp = await so_posisi.fakta_dp(conn, tenant_id, [r for r in so if r["status"] not in AKTIF_TIDAK])
        terkirim, _ = await so_kirim.terkirim_per_baris(conn, tenant_id, jalan)
        difaktur = {r["soi_id"]: _d(r["q"]) for r in await conn.fetch(SQL_FAKTUR_TERBIT_PER_BARIS_SO, tenant_id, jalan)}
        for b in await conn.fetch(SQL_BARIS, tenant_id, jalan):
            per_so.setdefault(b["so_id"], []).append({
                "quantity": b["quantity"], "perlu": b["perlu"], "stok": b["stok"],
                "terkirim": terkirim.get(b["id"], NOL), "difaktur": difaktur.get(b["id"], NOL)})
    out = {}
    for r in so:
        kd, nilai = dp.get(r["id"], (None, None))
        out[r["id"]] = {"aksi": aksi_so(r["status"], r["expected_ship_date"], hari_ini, per_so.get(r["id"], []),
                                        dp=kd, fase_lokasi=r.get("fase_lokasi")),
                        "total": _d(r["total_amount"]), "nilai": ({kd: nilai} if kd else {}),
                        "nomor": r.get("order_number")}
    return out


def id_aksi(f: dict, aksi: str) -> list:
    return [i for i, v in f.items() if aksi in v["aksi"]]


def ringkasan(f: dict) -> dict:
    """-> {<kunci>_count: int, <kunci>_value: float} untuk tiap AKSI (satu SO boleh di beberapa kunci).
    _value = Σ total SO; kunci DP memakai nilai DP-nya (fakta[..]["nilai"])."""
    out = {}
    for k in AKSI:
        isi = [v.get("nilai", {}).get(k, v["total"]) for v in f.values() if k in v["aksi"]]
        out[f"{k}_count"] = len(isi)
        out[f"{k}_value"] = float(sum(isi, NOL))
    return out


async def dp_belum_diterima(conn, tenant_id: str, hari_ini: date) -> list:
    """Kartu dashboard "DP belum diterima" + ?tugas=dp_belum_diterima (PERLU-MINTA-DP, 14 Okt 2026): menunggu_dp U
    perlu_minta_dp dari `fakta` yang SAMA dengan pill/summary. -> [{id, order_number, dp_wajib, kunci}], dp_wajib = nilai
    kunci itu (sama dengan summary _value)."""
    f = await fakta(conn, tenant_id, hari_ini)
    out = []
    for i, v in f.items():
        k = next((x for x in KUNCI_DP if x in v["aksi"]), None)
        if k:
            out.append({"id": i, "order_number": v["nomor"], "dp_wajib": v["nilai"][k], "kunci": k})
    return sorted(out, key=lambda r: r["order_number"] or "")
