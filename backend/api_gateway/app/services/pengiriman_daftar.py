"""PENJUALAN-SERAGAM / Pengiriman (Surat Jalan) (20 Okt 2026): data daftar/bundel dihitung SERVER, FE hanya memformat.

  serah_keadaan = belum_dicatat | belum_diterima | diterima | tanpa  (+ serah_teks netral: "Serah belum dicatat" / "Belum diterima" /
                  "Diterima 17 Okt" / "—"). tanpa = voided ATAU flag pengiriman_serah OFF. Pengiriman tak punya tanggal jatuh tempo -> bukan enum waktu.
  aksi (pill; putusan MASTER 20 Okt): perlu_resi (jenis kurir, nol resi) > belum_dicatat (TIDAK ada baris serah ATAU jenis_serah kosong =
                  data serah yang benar-benar kurang). `belum_diterima` BUKAN pill (diterima_pada = 0 di mana pun -> derau permanen; tiket).
  aksi per baris, ?aksi= dan hitungan summary memakai predikat SQL (sql_aksi) yang setara dgn serah_fakta (diuji berpasangan + harness nyata).
  position_text = kolom Status: voided -> "Dibatalkan", selain itu == serah_teks (serah_teks TETAP = kolom Serah). SATU fungsi untuk
                  baris daftar, detail, dan anchor /deliveries/{id}/documents (putusan MASTER 21 Okt 2026).
  documents     = kelompok bundel (kelompok_untuk) tanpa rekap -- SATU definisi dgn /deliveries/{id}/documents.
  perlu_difaktur (21 Okt 2026, MASTER; pola NetSuite Pending Billing, SATU definisi dgn so_aksi.perlu_ditagih / DEFINISI-TAGIH e90843af):
                  Surat Jalan asal 'so' (tanpa faktur), posted, yang punya baris SO 'perlu kirim' dengan [terkirim aktif - difaktur TERBIT] > 0,
                  dan tenant TIDAK memakai tagih_sebelum_kirim (flag MATI = bawaan). Subset dari pill SO perlu_ditagih. Bebas dari fitur serah.
                  Prioritas pill tunggal: perlu_resi > belum_dicatat > perlu_difaktur.
"""
from datetime import date
from typing import Optional

from .so_posisi import BULAN

AKSI_SERAH = ("perlu_resi", "belum_dicatat")  # urutan desak; hanya bila fitur serah aktif
AKSI = AKSI_SERAH + ("perlu_difaktur",)       # urutan desak seluruhnya
FLAG_TAGIH_SEBELUM_KIRIM = "tagih_sebelum_kirim"  # SAMA dgn so_aksi.FLAG_TAGIH_SEBELUM_KIRIM (tenant_features; MATI = tagih sesudah kirim)
TEKS = {"belum_dicatat": "Serah belum dicatat", "belum_diterima": "Belum diterima", "tanpa": "—"}


def sql_perlu_difaktur(f: str = "f") -> str:
    """Predikat SQL perlu_difaktur (alias `f` = invoice_fulfillments; $1 = tenant). Pemanggil WAJIB menggantinya dengan FALSE bila tenant memakai
    tagih_sebelum_kirim. Per baris SO: terkirim aktif (jalur faktur + jalur asal-so; = so_kirim.SQL_TERKIRIM_PER_BARIS) - qty faktur TERBIT
    (status NOT IN draft/void; = sj_so.PRED_FAKTUR_TERBIT) > 0, hanya baris 'perlu kirim' (= so_aksi.SQL_BARIS)."""
    return (
        f"({f}.status = 'posted' AND {f}.voided_at IS NULL AND {f}.asal = 'so' AND EXISTS ("
        "SELECT 1 FROM invoice_fulfillment_items pdi "
        "JOIN sales_order_items pds ON pds.id = pdi.sales_order_item_id "
        f"LEFT JOIN products pdp ON pdp.id = pds.item_id AND pdp.tenant_id = {f}.tenant_id "
        f"WHERE pdi.fulfillment_id = {f}.id AND COALESCE(pds.perlu_kirim, pdp.track_inventory, false) "
        "AND (COALESCE((SELECT SUM(x.q) FROM ("
        "SELECT ifj.quantity AS q FROM invoice_fulfillment_items ifj "
        "JOIN sales_invoice_items sij ON sij.id = ifj.invoice_item_id "
        "JOIN invoice_fulfillments f2 ON f2.id = ifj.fulfillment_id AND f2.tenant_id = $1 AND f2.voided_at IS NULL AND f2.status <> 'voided' "
        "WHERE sij.sales_order_item_id = pds.id "
        "UNION ALL SELECT ifk.quantity FROM invoice_fulfillment_items ifk "
        "JOIN invoice_fulfillments f3 ON f3.id = ifk.fulfillment_id AND f3.tenant_id = $1 AND f3.asal = 'so' AND f3.voided_at IS NULL AND f3.status <> 'voided' "
        "WHERE ifk.sales_order_item_id = pds.id) x), 0) "
        "- COALESCE((SELECT SUM(sq.quantity) FROM sales_invoice_items sq "
        "JOIN sales_invoices si2 ON sq.invoice_id = si2.id AND si2.tenant_id = $1 AND si2.status NOT IN ('draft', 'void') "
        "WHERE sq.sales_order_item_id = pds.id), 0)) > 0))")


def gabung_aksi(aksi_serah: Optional[str], perlu_difaktur: bool) -> Optional[str]:
    """Pure. Satu pill: aksi serah (perlu_resi > belum_dicatat) menang; perlu_difaktur hanya bila tak ada aksi serah."""
    return aksi_serah or ("perlu_difaktur" if perlu_difaktur else None)


async def tagih_sesudah_kirim(conn, tid: str) -> bool:
    """True = tenant memakai tagih-sesudah-kirim (flag tagih_sebelum_kirim TIDAK menyala; bawaan)."""
    return not await conn.fetchval(
        "SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled", tid, FLAG_TAGIH_SEBELUM_KIRIM)


async def perlu_difaktur_ids(conn, tid: str, ids: list, serah_aktif: bool = False) -> set:
    """Himpunan fulfillment_id (dari `ids`) yang pill-nya perlu_difaktur; kosong bila tenant memakai tagih_sebelum_kirim.
    `serah_aktif` True -> yang pill-nya aksi serah dikeluarkan (partisi, = sql_aksi)."""
    if not ids or not await tagih_sesudah_kirim(conn, tid):
        return set()
    return {r["id"] for r in await conn.fetch(
        f"SELECT f.id FROM invoice_fulfillments f WHERE f.tenant_id = $1 AND f.id = ANY($2::uuid[]) AND {sql_aksi('perlu_difaktur', 'f', serah_aktif)}",
        tid, list(ids))}


def sql_aksi(aksi: str, f: str = "f", serah_aktif: bool = False) -> str:
    """Predikat SQL (alias `f` = invoice_fulfillments; $1 = tenant) setara aksi_serah(...) == aksi.
    perlu_difaktur = PARTISI dgn pill serah: bila fitur serah aktif, baris yang pill-nya perlu_resi/belum_dicatat TIDAK ikut
    (satu baris = satu pill; ?aksi=, hitungan summary, dan `aksi` baris memakai predikat yang sama)."""
    if aksi == "perlu_difaktur":
        if serah_aktif:
            return f"({sql_perlu_difaktur(f)} AND NOT {sql_aksi('perlu_resi', f)} AND NOT {sql_aksi('belum_dicatat', f)})"
        return sql_perlu_difaktur(f)
    return {
        "perlu_resi": (f"({f}.status = 'posted' AND EXISTS (SELECT 1 FROM pengiriman_serah s WHERE s.tenant_id = $1 AND s.fulfillment_id = {f}.id "
                       f"AND s.jenis_serah = 'kurir') AND NOT EXISTS (SELECT 1 FROM pengiriman_resi r WHERE r.tenant_id = $1 AND r.fulfillment_id = {f}.id))"),
        "belum_dicatat": (f"({f}.status = 'posted' AND NOT EXISTS (SELECT 1 FROM pengiriman_serah s WHERE s.tenant_id = $1 "
                          f"AND s.fulfillment_id = {f}.id AND s.jenis_serah IS NOT NULL))"),
    }[aksi]


def serah_fakta(aktif: bool, status: Optional[str], jenis: Optional[str], diterima_pada: Optional[date], n_resi: int) -> dict:
    """Pure. jenis=None bila tak ada baris serah ATAU jenis kosong."""
    if not aktif or status != "posted":
        kd, aksi = "tanpa", None
    elif jenis is None:
        kd, aksi = "belum_dicatat", "belum_dicatat"
    else:
        kd = "diterima" if diterima_pada else "belum_diterima"
        aksi = "perlu_resi" if jenis == "kurir" and not n_resi else None
    if kd == "diterima":
        teks = f"Diterima {diterima_pada.day} {BULAN[diterima_pada.month - 1]}"
    else:
        teks = TEKS[kd]
    return {"serah_keadaan": kd, "serah_teks": teks, "aksi": aksi}


def position_text(status: Optional[str], serah_teks: str) -> str:
    return "Dibatalkan" if status == "voided" else serah_teks


async def fakta_serah(conn, tid: str, ids: list) -> dict:
    """{fulfillment_id: (jenis_serah|None, diterima_pada|None, n_resi)} untuk Surat Jalan yang punya baris serah."""
    if not ids:
        return {}
    return {r["fulfillment_id"]: (r["jenis_serah"], r["diterima_pada"], r["n_resi"]) for r in await conn.fetch(
        """SELECT s.fulfillment_id, s.jenis_serah, s.diterima_pada,
                  (SELECT COUNT(*) FROM pengiriman_resi r WHERE r.tenant_id = $1 AND r.fulfillment_id = s.fulfillment_id) AS n_resi
           FROM pengiriman_serah s WHERE s.tenant_id = $1 AND s.fulfillment_id = ANY($2::uuid[])""", tid, list(ids))}


async def kelompok_untuk(conn, tid: str, surat_jalan: list) -> dict:
    """{fulfillment_id: [kelompok]} bundel Surat Jalan: rekap · pnw (bila ber-SO) · ki = Surat Jalan ITU SENDIRI (juga yang voided,
    `batal` lewat rampungkan) + faktur asal. `surat_jalan` = [{id, invoice_id, sales_order_id}]. Pembentuk dokumen HANYA dari dokumen_bundel."""
    from . import dokumen_bundel as DB
    if not surat_jalan:
        return {}
    sj = {d["id"]: d for d in await DB.surat_jalan(conn, tid, [s["id"] for s in surat_jalan], termasuk_batal=True)}
    inv_ids = sorted({s["invoice_id"] for s in surat_jalan if s.get("invoice_id")})
    fak = {d["id"]: d for d in await DB.faktur(conn, tid, inv_ids)}
    so_ids = sorted({s["sales_order_id"] for s in surat_jalan if s.get("sales_order_id")})
    quote_per_so = {r["id"]: r["quote_id"] for r in await conn.fetch(
        "SELECT id, quote_id FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, so_ids)} if so_ids else {}
    pnw = {}
    hasil = {}
    for s in surat_jalan:
        kel = []
        so = s.get("sales_order_id")
        if so:
            kel.append({"key": "rekap", "label": "Rekap", "docs": [DB.rekap(so)]})
            qid = quote_per_so.get(so)
            if qid:
                if qid not in pnw:
                    pnw[qid] = await DB.penawaran(conn, tid, qid)
                if pnw[qid]:
                    kel.append({"key": "pnw", "label": "Penawaran", "docs": [dict(pnw[qid])]})
        ki = [sj[str(s["id"])]] if str(s["id"]) in sj else []
        if s.get("invoice_id") and str(s["invoice_id"]) in fak:
            ki.append(fak[str(s["invoice_id"])])
        if ki:
            kel.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})
        hasil[s["id"]] = kel
    return hasil


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel TANPA rekap."""
    return [{"type": d["kind"]} for g in kelompok if g["key"] != "rekap" for d in g["docs"]]
