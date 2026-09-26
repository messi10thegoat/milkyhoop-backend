"""Nota kredit atas pendapatan TERTUNDA (26 Sep 2026, V312; putusan MASTER P1+P2).

PSAK 72 (modifikasi kontrak / konsesi harga): porsi NK yang jatuh pada kewajiban yang BELUM dipenuhi (barang belum
dikirim -> pendapatan masih di Pendapatan Diterima Dimuka) mengurangi kewajiban kontrak, bukan pendapatan:
  Dr Dimuka (porsi tertunda U) + Dr Retur Penjualan (S − U) + Dr PPN / Cr Piutang,
  dan allocated_amount baris faktur turun U (pengakuan berikutnya saat kirim otomatis = nilai kontrak baru).
Porsi per baris = pro-rata (allocated − recognized) / allocated — konsesi harga menyebar ke unit terkirim & belum.
reason 'return'/'damaged' = barang KEMBALI -> sudah terkirim -> sudah diakui -> seluruhnya Retur (seperti dulu).
Porsi dicatat di credit_note_deferral_lines supaya void NK mencerminkan PERSIS.

Iron Laws: 1/16 (angka dari jurnal+sub-ledger alokasi yang sama dengan Check 16), 2 (void = jurnal pembalik),
9/25 (Decimal ROUND_HALF_UP, sisa pembulatan diserap baris terakhir), 13 (kunci INVOICE_FULFILL faktur yang sama
dengan fulfill -> recognized tak bergeser di tengah), 24 (tenant eksplisit), 27 (akun lewat peran), 31 (void membalik
SEMUA lapis: jurnal + allocated_amount).
"""
from decimal import ROUND_HALF_UP, Decimal
from fastapi import HTTPException

NOL = Decimal("0")
SEN = Decimal("0.01")
ALASAN_BARANG_KEMBALI = ("return", "damaged")


def _q(x) -> Decimal:
    return Decimal(str(x)).quantize(SEN, rounding=ROUND_HALF_UP)


def _bagi(total: Decimal, bobot: list) -> list:
    """total dibagi sebanding bobot (Decimal >= 0); sisa pembulatan ke entri berbobot terakhir. Σ hasil == total."""
    jml = sum(bobot, NOL)
    if total <= 0 or jml <= 0:
        return [NOL] * len(bobot)
    hasil, dipakai, akhir = [], NOL, max(i for i, b in enumerate(bobot) if b > 0)
    for i, b in enumerate(bobot):
        if i == akhir:
            hasil.append(total - dipakai)
        else:
            v = _q(total * b / jml)
            hasil.append(v)
            dipakai += v
    return hasil


def porsi_tertunda(subtotal_nk, item_nk: list, baris_faktur: list, reason) -> dict:
    """{invoice_item_id: U} — MURNI (diuji tanpa DB).
    subtotal_nk : Σ yang didebit ke pendapatan (total − pajak) — sama dengan baris Retur jurnal lama.
    item_nk     : [{"item_id", "subtotal"}] baris NK.
    baris_faktur: [{"id", "item_id", "allocated_amount", "recognized_amount"}] baris faktur asal.
    Tiap item NK -> baris faktur ber-item_id sama (tanpa item_id / tak cocok -> semua baris); porsi item =
    S_item × Σtertunda/Σalokasi, dibatasi sisa tertunda; lalu disebar ke baris sebanding sisa tertundanya."""
    if reason in ALASAN_BARANG_KEMBALI:
        return {}
    S = _q(subtotal_nk)
    if S <= 0 or not item_nk or not baris_faktur:
        return {}
    sisa = {b["id"]: max(_q(b["allocated_amount"] or 0) - _q(b["recognized_amount"] or 0), NOL) for b in baris_faktur}
    alok = {b["id"]: _q(b["allocated_amount"] or 0) for b in baris_faktur}
    sisa_awal = dict(sisa)   # RASIO dari keadaan faktur SEBELUM NK; sisa berjalan hanya untuk batas
    S_item = _bagi(S, [max(_q(i.get("subtotal") or 0), NOL) for i in item_nk])
    hasil = {}
    for it, s_it in zip(item_nk, S_item):
        if s_it <= 0:
            continue
        cocok = [b["id"] for b in baris_faktur if it.get("item_id") and b.get("item_id")
                 and str(b["item_id"]) == str(it["item_id"])] or [b["id"] for b in baris_faktur]
        A = sum((alok[k] for k in cocok), NOL)
        D0 = sum((sisa_awal[k] for k in cocok), NOL)
        D = sum((sisa[k] for k in cocok), NOL)
        if A <= 0 or D <= 0:
            continue
        U = min(_q(s_it * D0 / A), D)
        for k, u in zip(cocok, _bagi(U, [sisa[k] for k in cocok])):
            if u > 0:
                hasil[k] = hasil.get(k, NOL) + u
                sisa[k] -= u
    return hasil


async def kunci_faktur(conn, tenant_id: str, invoice_id):
    """Kunci yang SAMA dengan fulfill (sales_invoices.py) -> recognized_amount tak bergeser selama NK memecah."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                       tenant_id, f"INVOICE_FULFILL:{str(invoice_id)}")


async def hitung_untuk_nk(conn, tenant_id: str, cn, subtotal_nk) -> tuple:
    """-> (porsi {invoice_item_id: U}, baris_faktur {id: row}) untuk NK ber-faktur-asal; ({}, {}) bila tidak."""
    if not cn["original_invoice_id"] or cn["reason"] in ALASAN_BARANG_KEMBALI:
        return {}, {}
    await kunci_faktur(conn, tenant_id, cn["original_invoice_id"])
    baris = await conn.fetch(
        """SELECT sii.id, sii.item_id, sii.allocated_amount, sii.recognized_amount
           FROM sales_invoice_items sii JOIN sales_invoices si ON si.id = sii.invoice_id
           WHERE sii.invoice_id = $1 AND si.tenant_id = $2
           ORDER BY sii.line_number, sii.id FOR UPDATE OF sii""",
        cn["original_invoice_id"], tenant_id)
    item_nk = await conn.fetch(
        "SELECT item_id, subtotal FROM credit_note_items WHERE credit_note_id = $1 ORDER BY line_number, id", cn["id"])
    porsi = porsi_tertunda(subtotal_nk, [dict(i) for i in item_nk], [dict(b) for b in baris], cn["reason"])
    return porsi, {b["id"]: b for b in baris}


async def catat_porsi(conn, tenant_id: str, cn, journal_id, porsi: dict, baris: dict):
    """Sesudah jurnal NK POSTED (transaksi yang sama): allocated −= U + catat porsi."""
    for item_id, u in porsi.items():
        r = await conn.execute(
            """UPDATE sales_invoice_items SET allocated_amount = allocated_amount - $1
               WHERE id = $2 AND invoice_id = $3 AND allocated_amount - COALESCE(recognized_amount, 0) >= $1""",
            u, item_id, cn["original_invoice_id"])
        if r != "UPDATE 1":   # tak boleh terjadi di bawah kunci; gagal-keras (transaksi batal) daripada salah
            raise HTTPException(status_code=409, detail="Alokasi baris faktur berubah saat nota kredit diposting; ulangi")
        await conn.execute(
            """INSERT INTO credit_note_deferral_lines
                 (tenant_id, credit_note_id, invoice_id, invoice_item_id, amount, recognized_at_cn, journal_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            tenant_id, cn["id"], cn["original_invoice_id"], item_id, u,
            _q(baris[item_id]["recognized_amount"] or 0), journal_id)


async def pulihkan_saat_void(conn, tenant_id: str, cn, reversal_journal_id=None, periksa_saja=False):
    """Void NK: allocated += porsi. DITOLAK 409 bila pengakuan baris bertambah sesudah NK (barang terkirim sesudah
    NK -> porsi yang dikembalikan tak punya pengiriman tersisa = Dimuka terdampar lagi; buat faktur/nota debit baru).
    periksa_saja=True: hanya penjagaan (dipanggil SEBELUM jurnal pembalik ditulis)."""
    porsi = await conn.fetch(
        """SELECT d.id, d.invoice_item_id, d.amount, d.recognized_at_cn, d.invoice_id,
                  COALESCE(sii.recognized_amount, 0) AS recognized_now
           FROM credit_note_deferral_lines d JOIN sales_invoice_items sii ON sii.id = d.invoice_item_id
           WHERE d.credit_note_id = $1 AND d.tenant_id = $2 AND d.reversed_at IS NULL""",
        cn["id"], tenant_id)
    if not porsi:
        return NOL
    await kunci_faktur(conn, tenant_id, porsi[0]["invoice_id"])
    bergeser = [p for p in porsi if _q(p["recognized_now"]) > _q(p["recognized_at_cn"])]
    if bergeser:
        raise HTTPException(status_code=409, detail={
            "code": "CN_VOID_AFTER_DELIVERY",
            "message": ("Barang faktur ini sudah dikirim sesudah nota kredit diposting; nota kredit tak bisa "
                        "dibatalkan. Buat faktur atau nota debit baru untuk selisihnya."),
        })
    if periksa_saja:
        return sum((_q(p["amount"]) for p in porsi), NOL)
    for p in porsi:
        await conn.execute("UPDATE sales_invoice_items SET allocated_amount = allocated_amount + $1 WHERE id = $2",
                           p["amount"], p["invoice_item_id"])
        await conn.execute("UPDATE credit_note_deferral_lines SET reversed_at = now(), reversal_journal_id = $1 "
                           "WHERE id = $2", reversal_journal_id, p["id"])
    return sum((_q(p["amount"]) for p in porsi), NOL)
