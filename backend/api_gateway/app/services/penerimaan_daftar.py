"""PENJUALAN-SERAGAM / Penerimaan Pembayaran (20 Okt 2026): data daftar/bundel dihitung SERVER, FE hanya memformat.

  dialokasikan       = Σ alokasi AKTIF ("Ke faktur"; fungsi SAMA dgn bruto_dialokasi / anchor.paid bundel); hanya wrapper posted, lainnya 0.
                       dialokasikan + belum_dialokasikan == total_amount (posted wrapper).
  belum_dialokasikan = GREATEST(receive_payments.total_amount − Σ alokasi AKTIF, 0) -- TURUNAN alokasi, BUKAN cache wrapper
                       (`allocated_amount`/`unapplied_amount` basi untuk penerimaan yang alokasinya dilepas: kaos 6 baris, 20 Okt).
                       Hanya penerimaan wrapper berstatus posted; lainnya 0.   (Law 16: angka dari data sumber, bukan cache)
  aksi               = 'belum_dialokasikan' bila belum_dialokasikan > 0 (pill; ?aksi= dan hitungan summary memakai SQL yang SAMA)
  alokasi_keadaan    = belum | sebagian | penuh | tanpa (draf/void/non-wrapper) ; alokasi_teks = teks persis putusan MASTER
  documents          = kelompok bundel (kelompok_untuk) tanpa rekap -- SATU definisi dgn /receive-payments/{id}/documents
  ?search=           = routers/receive_payments._syarat_cari (SATU sumber daftar & /summary?search=)
"""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")
TOLERANSI = Decimal("0.005")
AKSI = ("belum_dialokasikan",)
TEKS = {"belum": "Belum dialokasikan", "sebagian": "Sebagian dialokasikan", "penuh": "Teralokasi penuh", "tanpa": "—"}


def sql_dialokasi(rp: str) -> str:
    """Σ alokasi AKTIF penerimaan `rp` (alias tabel receive_payments)."""
    return f"COALESCE((SELECT SUM(a.amount_applied) FROM receive_payment_allocations a WHERE a.payment_id = {rp}.id AND a.status = 'active'), 0)"


def sql_bruto(rp: str) -> str:
    """Jumlah EFEKTIF yang harus teralokasi = uang diterima + potongan (rumus tulis: unapplied = (total_amount + discount_amount) - alokasi)."""
    return f"({rp}.total_amount + COALESCE({rp}.discount_amount, 0))"


def sql_belum(rp: str) -> str:
    return f"GREATEST({sql_bruto(rp)} - {sql_dialokasi(rp)}, 0)"


def alokasi_fakta(wrapper: bool, status: Optional[str], bruto, dialokasi) -> dict:
    """Pure: bruto = receive_payments.total_amount + discount_amount (jumlah efektif), dialokasi = Σ alokasi aktif (keduanya None untuk non-wrapper)."""
    if not wrapper or status != "posted" or bruto is None:
        belum, kd, ke_faktur = NOL, "tanpa", NOL
    else:
        ke_faktur = Decimal(str(dialokasi or 0))
        belum = max(Decimal(str(bruto)) - ke_faktur, NOL)
        kd = "penuh" if belum <= TOLERANSI else ("belum" if belum >= Decimal(str(bruto)) - TOLERANSI else "sebagian")
    return {"dialokasikan": float(ke_faktur), "belum_dialokasikan": float(belum), "aksi": "belum_dialokasikan" if belum > TOLERANSI else None,
            "alokasi_keadaan": kd, "alokasi_teks": TEKS[kd]}


def dengan_turunan(fakta: dict) -> dict:
    """KOREKSI cache (21 Okt 2026, izin pemilik): untuk penerimaan wrapper POSTED `allocated_amount` = Σ alokasi aktif dan `unapplied_amount` =
    belum_dialokasikan (TURUNAN, fungsi SAMA dgn alokasi_fakta); draf/voided/non-wrapper tak berubah (kunci tak ditambahkan). Kolom cache DB tetap ada."""
    if fakta["alokasi_keadaan"] == "tanpa":
        return dict(fakta)
    return {**fakta, "allocated_amount": fakta["dialokasikan"], "unapplied_amount": fakta["belum_dialokasikan"]}


async def bruto_dialokasi(conn, tid: str, ids: list) -> dict:
    """{payment_id: (total_amount + discount_amount, Σ alokasi aktif)} untuk penerimaan wrapper -- formula SAMA dgn sql_belum (sql_dialokasi)."""
    if not ids:
        return {}
    return {r["id"]: (r["bruto"], r["dialokasi"]) for r in await conn.fetch(
        f"SELECT rp.id, {sql_bruto('rp')} AS bruto, {sql_dialokasi('rp')} AS dialokasi FROM receive_payments rp "
        "WHERE rp.tenant_id = $1 AND rp.id = ANY($2::uuid[])", tid, list(ids))}


async def kelompok_untuk(conn, tid: str, pembayaran: list) -> dict:
    """{payment_id: [kelompok]} bundel penerimaan, urutan = bundel faktur/SO: rekap · pnw · kw · ki · nk. `pembayaran` = [{id, ...}] baris
    receive_payments milik tenant. Pembentuk dokumen HANYA dari services/dokumen_bundel (tak ada SQL bentuk dokumen di sini);
    yang dikueri di sini hanya PEMILIHAN id (alokasi aktif, surat jalan/nota kredit/SO faktur)."""
    from . import dokumen_bundel as DB
    ids = [p["id"] for p in pembayaran]
    if not ids:
        return {}
    alok = {}
    for r in await conn.fetch(
            "SELECT payment_id, invoice_id FROM receive_payment_allocations WHERE tenant_id = $1 AND payment_id = ANY($2::uuid[]) AND status = 'active'",
            tid, ids):
        alok.setdefault(r["payment_id"], []).append(r["invoice_id"])
    inv = sorted({i for v in alok.values() for i in v})
    fak = {d["id"]: d for d in await DB.faktur(conn, tid, inv)}
    sj_per_inv, nk_per_inv, so_inv = {}, {}, {}
    if inv:
        for r in await conn.fetch("SELECT id, invoice_id FROM invoice_fulfillments WHERE tenant_id = $1 AND invoice_id = ANY($2::uuid[])", tid, inv):
            sj_per_inv.setdefault(r["invoice_id"], []).append(r["id"])
        for r in await conn.fetch(
                "SELECT id, original_invoice_id FROM credit_notes WHERE tenant_id = $1 AND original_invoice_id = ANY($2::uuid[]) AND status NOT IN ('draft', 'void')",
                tid, inv):
            nk_per_inv.setdefault(r["original_invoice_id"], []).append(r["id"])
        for r in await conn.fetch("SELECT id, sales_order_id FROM sales_invoices WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, inv):
            so_inv[r["id"]] = r["sales_order_id"]
    sj = {d["id"]: d for d in await DB.surat_jalan(conn, tid, [i for v in sj_per_inv.values() for i in v])}
    nk = {d["id"]: d for d in await DB.nota_kredit(conn, tid, [i for v in nk_per_inv.values() for i in v])}
    so_ids = sorted({v for v in so_inv.values() if v})
    quote_per_so = {r["id"]: r["quote_id"] for r in await conn.fetch(
        "SELECT id, quote_id FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, so_ids)} if so_ids else {}
    pnw_cache = {}
    kw = await DB.kwitansi_penerimaan_sendiri(conn, tid, ids)
    urut = lambda ds: sorted(ds, key=lambda d: (d["date"] or "", d["number"] or ""))  # noqa: E731  (= ORDER BY tanggal, nomor builder)
    hasil = {}
    for pid in ids:
        invs = alok.get(pid, [])
        sos = {so_inv.get(i) for i in invs}
        kel = []
        if invs and len(sos) == 1 and None not in sos:
            so = next(iter(sos))
            kel.append({"key": "rekap", "label": "Rekap", "docs": [DB.rekap(so)]})
            qid = quote_per_so.get(so)
            if qid:
                if qid not in pnw_cache:
                    pnw_cache[qid] = await DB.penawaran(conn, tid, qid)
                if pnw_cache[qid]:
                    kel.append({"key": "pnw", "label": "Penawaran", "docs": [dict(pnw_cache[qid])]})
        if pid in kw:
            kel.append({"key": "kw", "label": "Pembayaran", "planned": None, "docs": [kw[pid]]})
        ki = urut([sj[str(s)] for i in invs for s in sj_per_inv.get(i, []) if str(s) in sj])
        ki += urut([fak[str(i)] for i in invs if str(i) in fak])
        if ki:
            kel.append({"key": "ki", "label": "Kirim & Faktur", "docs": ki})
        nkd = urut([nk[str(c)] for i in invs for c in nk_per_inv.get(i, []) if str(c) in nk])
        if nkd:
            kel.append({"key": "nk", "label": "Nota Kredit", "docs": nkd})
        hasil[pid] = kel
    return hasil


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel TANPA rekap (pola kolom Dok. Pesanan)."""
    return [{"type": d["kind"]} for g in kelompok if g["key"] != "rekap" for d in g["docs"]]
