"""Reklasifikasi KELAS BEBAN pembelian (RECLASS-COGS-CLASS, 30 Okt 2026; aturan + koreksi riwayat disetujui LANGSUNG pemilik).

Debit FPB lama bisa berada di akun beban yang SALAH kelas: barang non-stok di COGS_SERVICE (jalur buat-langsung-posted lama) atau jasa di
COGS_SALES (reklas Persediaan lama JV RECLASSIFY_BILL_INVENTORY yang memukul rata ke HPP). Modul ini memindahkannya lewat SATU jurnal
penyesuaian bertanggal hari ini -- jurnal lama TAK disentuh (Law 2), periode tertutup tak disentuh (Law 5), bisa dibalik lewat
rute yang sama (reverse_journal_id -> balik()).

Calon DITURUNKAN dari data, bukan angka tulis tangan, generik untuk semua tenant:
  * TARGET per FPB = tagihan_aksi.hitung_debit (penentu SATU posting FPB hari ini) dibatasi ke akun COGS_SALES + COGS_SERVICE;
  * POSISI per FPB = debit-kredit akun yang sama dari jurnal BILL efektif + baris reklas Persediaan lama efektif yang memo-nya menyebut
    nomor FPB ("Reklas {nomor} ...") + baris jurnal penanda ini yang efektif (memo memuat id FPB);
  * calon HANYA bila total COGS_SALES+COGS_SERVICE posisi == target (cuma beda kelas). Beda total (mis. masih di Persediaan, akun produk
    khusus, GRN) -> DILEWATI dengan alasan, tidak ditebak.
Pembalik: balik() -- BUKAN /journals/{id}/reverse (jalur umum itu menolak akun HPP, pagar Gate 5); hanya jurnal berpenanda ini.
Idempoten tanpa migrasi: penanda deskripsi jurnal 'RECLASS-COGS-CLASS' + id FPB di memo baris. Sesudah diposting, posisi == target ->
pratinjau 0 calon; posting ulang -> 409 "sudah direklas". Tenant SELALU eksplisit (gateway BYPASSRLS)."""
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

from . import tagihan_aksi as TA
from .role_resolver import AccountRole

PENANDA = "RECLASS-COGS-CLASS"
SUMBER = "ADJUSTMENT"
NOL = Decimal("0")

SQL_FPB = """
SELECT b.id, b.invoice_number, COALESCE(b.grand_total, b.amount, 0) - COALESCE(b.tax_amount, 0) AS subtotal, j.id AS jid  -- == blok_posting
FROM bills b JOIN journal_entries j ON j.id = b.journal_id AND j.tenant_id = $1
WHERE b.tenant_id = $1 AND j.source_type = 'BILL' AND j.status = 'POSTED' AND j.reversed_by_id IS NULL
  AND COALESCE(b.is_opening_balance, false) = false
ORDER BY b.issue_date, b.invoice_number
"""
# Posisi akun kelas ($1 tenant, $2 id FPB TEKS, $3 nomor FPB, $4 jurnal BILL, $5 akun[]): jurnal BILL + reklas Persediaan lama + penanda ini.
SQL_POSISI = """
SELECT jl.account_id, SUM(jl.debit - jl.credit) AS net
FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id AND je.tenant_id = $1
WHERE je.status = 'POSTED' AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL AND jl.account_id = ANY($5::uuid[])
  AND (je.id = $4
       OR (je.source_type = 'RECLASSIFY_BILL_INVENTORY' AND starts_with(jl.memo, 'Reklas ' || $3 || ' '))
       OR (je.source_type = 'ADJUSTMENT' AND starts_with(je.description, 'RECLASS-COGS-CLASS')
           AND strpos(jl.memo, '(' || $2::text || ')') > 0))
GROUP BY jl.account_id
"""


async def _peran(conn, tid: str, role) -> Optional[object]:
    return await conn.fetchval("SELECT account_id FROM account_roles WHERE tenant_id = $1 AND role_key = $2", tid, role)


async def rencana(conn, tid: str) -> dict:
    """BACA SAJA. -> {moves[{bill_id, invoice_number, dari, ke, dari_kode, ke_kode, amount}], lewati[{invoice_number, alasan}], akun}."""
    sales, servis = await _peran(conn, tid, AccountRole.COGS_SALES), await _peran(conn, tid, AccountRole.COGS_SERVICE)
    inv = await _peran(conn, tid, AccountRole.INVENTORY_MERCHANDISE)
    if not sales or not servis:
        return {"moves": [], "lewati": [{"invoice_number": None, "alasan": "peran COGS_SALES/COGS_SERVICE belum dipetakan"}], "akun": {}}
    kode = {r["id"]: r["account_code"] for r in await conn.fetch(
        "SELECT id, account_code FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, [sales, servis])}
    moves, lewati = [], []
    for b in await conn.fetch(SQL_FPB, tid):
        try:
            target_baris = await TA.hitung_debit(conn, tid, b["id"], Decimal(str(b["subtotal"])), inv)
        except HTTPException as e:
            lewati.append({"invoice_number": b["invoice_number"], "alasan": f"penentu menolak: {getattr(e, 'detail', e)}"})
            continue
        tgt = {sales: NOL, servis: NOL}
        for aid, amt, _inv in target_baris:
            if aid in tgt:
                tgt[aid] += Decimal(str(amt))
        pos = {sales: NOL, servis: NOL}
        for r in await conn.fetch(SQL_POSISI, tid, str(b["id"]), b["invoice_number"], b["jid"], [sales, servis]):  # $2 dibaca sbg teks (memo)
            pos[r["account_id"]] = Decimal(str(r["net"] or 0))
        if tgt[sales] == pos[sales] and tgt[servis] == pos[servis]:
            continue   # sudah benar
        if tgt[sales] + tgt[servis] != pos[sales] + pos[servis]:
            lewati.append({"invoice_number": b["invoice_number"], "alasan": "total kelas beban beda dari penentu (bukan sekadar beda kelas)",
                           "posisi": {kode[sales]: str(pos[sales]), kode[servis]: str(pos[servis])},
                           "target": {kode[sales]: str(tgt[sales]), kode[servis]: str(tgt[servis])}})
            continue
        selisih = tgt[sales] - pos[sales]   # >0: COGS_SERVICE -> COGS_SALES; <0: sebaliknya
        dari, ke = (servis, sales) if selisih > 0 else (sales, servis)
        moves.append({"bill_id": str(b["id"]), "invoice_number": b["invoice_number"], "dari": dari, "ke": ke,
                      "dari_kode": kode[dari], "ke_kode": kode[ke], "amount": abs(selisih)})
    return {"moves": moves, "lewati": lewati, "akun": {"COGS_SALES": kode[sales], "COGS_SERVICE": kode[servis]}}


def _tampil(moves: list) -> list:
    return [{k: (float(v) if isinstance(v, Decimal) else (str(v) if k in ("dari", "ke") else v)) for k, v in m.items()} for m in moves]


def baris_jurnal(moves: list) -> list:
    """[(account_id, debit, credit, memo)] -- per FPB: Dr akun kelas benar / Cr akun kelas salah, memo memuat nomor + (id) FPB."""
    out = []
    for m in moves:
        memo = f"{PENANDA} {m['invoice_number']} ({m['bill_id']}) {m['dari_kode']}->{m['ke_kode']}"
        out.append((m["ke"], m["amount"], NOL, memo))
        out.append((m["dari"], NOL, m["amount"], memo))
    return out


async def jalankan(conn, tid: str, user_id, dry_run: bool = True, alasan: str = "", tanggal=None) -> dict:
    """Satu transaksi + kunci per tenant. dry_run -> pratinjau baris PERSIS (nol tulis). Tulis: periode wajib terbuka, DRAFT -> POSTED."""
    from ..utils.tanggal_tenant import tanggal_dokumen
    tanggal = tanggal or await tanggal_dokumen(conn, tid)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{PENANDA}:{tid}")
        r = await rencana(conn, tid)
        moves, total = r["moves"], sum((m["amount"] for m in r["moves"]), NOL)
        baris = baris_jurnal(moves)
        pratinjau = {"tanggal": str(tanggal), "total": float(total), "moves": _tampil(moves), "lewati": r["lewati"], "akun": r["akun"],
                     "lines": [{"account_id": str(a), "debit": float(d), "credit": float(k), "memo": m} for a, d, k, m in baris]}
        if dry_run:
            return {"status": "dry_run", **pratinjau}
        if not moves:
            sudah = await conn.fetchval(
                "SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = $2 AND description LIKE $3 "
                "AND status = 'POSTED' AND reversed_by_id IS NULL", tid, SUMBER, f"{PENANDA}%")
            if sudah:
                raise HTTPException(status_code=409, detail={"code": "SUDAH_DIREKLAS", "message": "Kelas beban pembelian sudah direklas."})
            return {"status": "nothing_to_reclassify", **pratinjau}
        periode = await conn.fetchrow("SELECT id, status FROM fiscal_periods WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date "
                                      "ORDER BY start_date DESC LIMIT 1", tid, tanggal)
        if periode and periode["status"] in ("CLOSED", "LOCKED"):
            raise HTTPException(status_code=400, detail={"code": "PERIODE_TERTUTUP", "message": f"Periode {periode['status']} untuk {tanggal}."})
        nomor = await conn.fetchval("SELECT get_next_journal_number($1, 'JV', $2)", tid, tanggal)
        desk = (f"{PENANDA}: reklas kelas beban pembelian {len(moves)} FPB "
                f"({', '.join(m['invoice_number'] for m in moves)}). {alasan}").strip()[:500]
        jid = await conn.fetchval(
            """INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, total_debit, total_credit,
                                            status, period_id, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $6, 'DRAFT', $7, $8) RETURNING id""",
            tid, nomor, tanggal, desk, SUMBER, total, periode["id"] if periode else None, str(user_id) if user_id else None)
        for i, (aid, d, k, memo) in enumerate(baris, 1):
            await conn.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, memo, debit, credit) VALUES ($1,$2,$3,$4,$5,$6)",
                               jid, i, aid, memo, d, k)
        await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", jid)
        return {"status": "posted", "journal_id": str(jid), "journal_number": nomor, **pratinjau}


async def balik(conn, tid: str, user_id, journal_id, alasan: str, tanggal=None) -> dict:
    """Pembalik jurnal penanda ini (cermin POST /journals/{id}/reverse: jurnal baru reversal_of_id + baris ditukar, DRAFT -> POSTED, asli
    reversed_by_id). Jalur umum /reverse menolak akun HPP (pagar_akun_modul, Gate 5) -- yang dibalik di sini HANYA jurnal ADJUSTMENT
    berpenanda RECLASS-COGS-CLASS milik tenant ini yang belum dibalik; nominal = baris tersimpan (bukan input klien)."""
    from uuid import UUID

    from ..utils.tanggal_tenant import tanggal_dokumen
    tanggal = tanggal or await tanggal_dokumen(conn, tid)
    try:
        jid = journal_id if isinstance(journal_id, UUID) else UUID(str(journal_id))
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "JURNAL_TAK_ADA", "message": "Jurnal reklas tidak ditemukan."})
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{PENANDA}:{tid}")
        j = await conn.fetchrow("""SELECT id, journal_number, status, reversed_by_id, reversal_of_id, source_type, description FROM journal_entries
                                   WHERE id = $1 AND tenant_id = $2 FOR UPDATE""", jid, tid)
        if not j or j["source_type"] != SUMBER or not (j["description"] or "").startswith(PENANDA) or j["reversal_of_id"] is not None:
            raise HTTPException(status_code=404, detail={"code": "JURNAL_TAK_ADA", "message": "Jurnal reklas tidak ditemukan."})
        if j["status"] != "POSTED" or j["reversed_by_id"] is not None:
            raise HTTPException(status_code=409, detail={"code": "SUDAH_DIBALIK", "message": f"Jurnal {j['journal_number']} sudah dibalik."})
        periode = await conn.fetchrow("SELECT id, status FROM fiscal_periods WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date "
                                      "ORDER BY start_date DESC LIMIT 1", tid, tanggal)
        if periode and periode["status"] in ("CLOSED", "LOCKED"):
            raise HTTPException(status_code=400, detail={"code": "PERIODE_TERTUTUP", "message": f"Periode {periode['status']} untuk {tanggal}."})
        baris = await conn.fetch("SELECT account_id, memo, debit, credit FROM journal_lines WHERE journal_id = $1 ORDER BY line_number", jid)
        total = sum((Decimal(str(b["debit"])) for b in baris), NOL)
        nomor = await conn.fetchval("SELECT get_next_journal_number($1, 'JV', $2)", tid, tanggal)
        rid = await conn.fetchval(
            """INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, total_debit, total_credit, status,
                                            period_id, reversal_of_id, reversal_reason, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $6, 'DRAFT', $7, $8, $9, $10) RETURNING id""",
            tid, nomor, tanggal, f"{PENANDA} BALIK {j['journal_number']}: {alasan}"[:500], SUMBER, total, periode["id"] if periode else None,
            jid, alasan or "balik reklas", str(user_id) if user_id else None)
        for i, b in enumerate(baris, 1):
            await conn.execute("INSERT INTO journal_lines (journal_id, line_number, account_id, memo, debit, credit) VALUES ($1,$2,$3,$4,$5,$6)",
                               rid, i, b["account_id"], b["memo"], b["credit"], b["debit"])
        await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", rid)
        await conn.execute("UPDATE journal_entries SET reversed_by_id = $3, reversed_at = NOW() WHERE id = $1 AND tenant_id = $2", jid, tid, rid)
        return {"status": "reversed", "journal_id": str(rid), "journal_number": nomor, "reversal_of": j["journal_number"], "total": float(total)}
