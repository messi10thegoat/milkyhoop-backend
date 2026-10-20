"""PENJUALAN-SERAGAM / Pengiriman (Surat Jalan) (20 Okt 2026): data daftar/bundel dihitung SERVER, FE hanya memformat.

  serah_keadaan = belum_dicatat | belum_diterima | diterima | tanpa  (+ serah_teks netral: "Serah belum dicatat" / "Belum diterima" /
                  "Diterima 17 Okt" / "—"). tanpa = voided ATAU flag pengiriman_serah OFF. Pengiriman tak punya tanggal jatuh tempo -> bukan enum waktu.
  aksi (pill; putusan MASTER 20 Okt): perlu_resi (jenis kurir, nol resi) > belum_dicatat (TIDAK ada baris serah ATAU jenis_serah kosong =
                  data serah yang benar-benar kurang). `belum_diterima` BUKAN pill (diterima_pada = 0 di mana pun -> derau permanen; tiket).
  aksi per baris, ?aksi= dan hitungan summary memakai predikat SQL (sql_aksi) yang setara dgn serah_fakta (diuji berpasangan + harness nyata).
  documents     = kelompok bundel (kelompok_untuk) tanpa rekap -- SATU definisi dgn /deliveries/{id}/documents.
"""
from datetime import date
from typing import Optional

from .so_posisi import BULAN

AKSI = ("perlu_resi", "belum_dicatat")  # urutan desak
TEKS = {"belum_dicatat": "Serah belum dicatat", "belum_diterima": "Belum diterima", "tanpa": "—"}


def sql_aksi(aksi: str, f: str = "f") -> str:
    """Predikat SQL (alias `f` = invoice_fulfillments; $1 = tenant) setara aksi_serah(...) == aksi."""
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
