"""PENJUALAN-SERAGAM / Proforma (21 Okt 2026): data daftar/bundel dihitung SERVER, FE hanya memformat.

  jatuh_tempo_terbuka = terbit ∧ SO tak batal ∧ belum lunas ∧ bertanggal (= aksi_proforma lewat_tempo/segera_tempo/menunggu_bayar bertanggal)
  jatuh_tempo_hari    = due_date - hari ini (tanggal BISNIS tenant), None bila tak terbuka; <0 == aksi lewat_tempo
  jatuh_tempo_keadaan = telat | hari_ini | akan | selesai (terbit LUNAS bertanggal; tanggal redup) | tanpa (draf/batal/kedaluwarsa/terbit pada SO batal/tanpa tanggal)
  jatuh_tempo_teks    = "Hari ini" / "Besok" / "24 Okt" / "12 Okt, lewat tempo" / "—"
  sisa/terbayar       = SUMBER YANG SAMA dgn kolom paid/aksi/summary (routers/proformas.kelas_bayar_terbit, terbayar_proforma)
  position_text       = kolom Status (Draf · Dibatalkan · Kedaluwarsa · Lunas · Dibayar sebagian · Menunggu bayar) -- SATU fungsi untuk
                        baris daftar, detail, dan anchor /proformas/{id}/documents (21 Okt 2026)
  documents           = kelompok bundel (kelompok_untuk) tanpa rekap -- SATU definisi dgn /proformas/{id}/documents
  ?search=            = routers/proformas._syarat_cari (SATU sumber daftar & /summary?search=)
"""
from datetime import date
from typing import Optional

from .so_posisi import BULAN


def jatuh_tempo_fakta(status: Optional[str], so_status: Optional[str], lunas: bool, due_date: Optional[date], hari_ini: date) -> dict:
    tgl = f"{due_date.day} {BULAN[due_date.month - 1]}" if due_date is not None else None
    terbuka = status == "issued" and so_status != "cancelled" and not lunas and due_date is not None
    hari = (due_date - hari_ini).days if terbuka else None
    if terbuka:
        kd = "telat" if hari < 0 else "hari_ini" if hari == 0 else "akan"
        teks = f"{tgl}, lewat tempo" if hari < 0 else "Hari ini" if hari == 0 else "Besok" if hari == 1 else tgl
    elif status == "issued" and lunas and due_date is not None:
        kd, teks = "selesai", tgl
    else:
        kd, teks = "tanpa", "—"
    return {"jatuh_tempo_terbuka": terbuka, "jatuh_tempo_hari": hari, "jatuh_tempo_keadaan": kd, "jatuh_tempo_teks": teks}


async def kelompok_untuk(conn, tid: str, proformas: list) -> dict:
    """{proforma_id: [kelompok]} bundel Proforma: rekap · pnw (penawaran SO) · pro = proforma ITU SENDIRI (draf/batal pun; term & lunas
    relatif SO) · kw = kwitansi uang muka bertaut ke proforma ini. `proformas` = [{id, sales_order_id}]. Pembentuk dokumen HANYA dari
    services/dokumen_bundel; yang dikueri di sini hanya PEMILIHAN id (status/penawaran SO, uang muka bertaut)."""
    from . import dokumen_bundel as DB
    if not proformas:
        return {}
    so_ids = sorted({p["sales_order_id"] for p in proformas})
    so = {r["id"]: r for r in await conn.fetch(
        "SELECT id, quote_id, status FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, so_ids)}
    pro_per_so = {}
    for sid in so_ids:
        batal = (so.get(sid) or {}).get("status") == "cancelled"
        pro_per_so[sid] = {d["id"]: d for d in await DB.proforma_so(conn, tid, sid, batal, termasuk_semua=True)}
    dep = {}
    for r in await conn.fetch("SELECT id, proforma_id FROM customer_deposits WHERE tenant_id = $1 AND proforma_id = ANY($2::uuid[]) AND status <> 'void'",
                              tid, [p["id"] for p in proformas]):
        dep.setdefault(r["proforma_id"], []).append(r["id"])
    kw = await DB.kwitansi_uang_muka(conn, tid, [i for v in dep.values() for i in v])
    pnw = {}
    hasil = {}
    for p in proformas:
        kel = []
        s = so.get(p["sales_order_id"])
        if s:
            kel.append({"key": "rekap", "label": "Rekap", "docs": [DB.rekap(p["sales_order_id"])]})
            if s["quote_id"]:
                if s["quote_id"] not in pnw:
                    pnw[s["quote_id"]] = await DB.penawaran(conn, tid, s["quote_id"])
                if pnw[s["quote_id"]]:
                    kel.append({"key": "pnw", "label": "Penawaran", "docs": [dict(pnw[s["quote_id"]])]})
        pro = pro_per_so.get(p["sales_order_id"], {}).get(str(p["id"]))
        if pro:
            kel.append({"key": "pro", "label": "Tagihan", "docs": [dict(pro)]})
        k = DB.urut_kwitansi({i: kw[i] for i in dep.get(p["id"], []) if i in kw})
        if k:
            kel.append({"key": "kw", "label": "Pembayaran", "planned": None, "docs": k})
        hasil[p["id"]] = kel
    return hasil


POSISI_PROFORMA = {"draft": "Draf", "cancelled": "Dibatalkan", "expired": "Kedaluwarsa"}


def position_text(status: Optional[str], nominal, terbayar) -> str:
    """Teks kolom Status. Status non-terbit -> POSISI_PROFORMA; terbit -> keadaan bayar (lunas = proforma_lunas, rumus tunggal)."""
    from decimal import Decimal
    from .proforma_aksi import proforma_lunas
    if status in POSISI_PROFORMA:
        return POSISI_PROFORMA[status]
    if proforma_lunas(nominal, terbayar):
        return "Lunas"
    return "Dibayar sebagian" if Decimal(str(terbayar or 0)) > 0 else "Menunggu bayar"


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel TANPA rekap."""
    return [{"type": d["kind"]} for g in kelompok if g["key"] != "rekap" for d in g["docs"]]
