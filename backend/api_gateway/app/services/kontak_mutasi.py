"""MUTASI piutang / hutang per kontak (buku besar pembantu, pola Accurate "rincian piutang/hutang per pelanggan/pemasok") -- kontrak
backend/docs/KONTRAK-kontak-mutasi-20261109.md (putusan MASTER 9 Nov 2026: tampilan baca-saja dari jurnal yang ada, BUKAN aturan uang baru).

journal_lines tak menyimpan pihak, jadi baris = komponen YANG SAMA dengan penentu saldo compute_ar_outstanding / compute_ap_outstanding
(cabang demi cabang; jurnal POSTED, tak dibalik; pihak dari dokumen sumber). Paritas dijamin konstruksi: Σ(debit - kredit) pelanggan
(kredit - debit vendor) seluruh baris == Σ outstanding pihak == `saldo` detail kontak (kontak_daftar). Fungsi DB berubah -> cabang di sini
WAJIB ikut (penjaga: uji salinan paritas semua kontak kaos/nyawit).

tanpa_batal=False menambah jurnal yang DIBATALKAN + pembaliknya berpasangan (batal=True, neto nol) -- paritas tetap. Akun = Piutang usaha
(RECEIVABLE) / Hutang usaha (PAYABLE). Uang muka / saldo kredit = akun lain, bukan di sini. Decimal (Law 9); tenant eksplisit di setiap kueri.
"""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")


def _akun(tipe: str) -> str:
    return f"JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = '{tipe}'"


HIDUP = "je.tenant_id = $1 AND je.status = 'POSTED' AND je.reversed_by_id IS NULL"
KOLOM = "urut, st, tgl, jid, jno, tipe, did, dno, ket, debit, kredit"

# ---------------------------------------------------------------- PELANGGAN (cermin compute_ar_outstanding)
AR = _akun("RECEIVABLE")
SQL_AR = f"""
WITH ai AS (SELECT id, invoice_number FROM sales_invoices WHERE tenant_id = $1 AND customer_id = $2 AND status NOT IN ('draft', 'void')),
pb AS (  -- payment_credits cabang 1: kredit piutang jurnal penerimaan, dibagi PROPORSIONAL per alokasi (V245)
    SELECT rp.id AS pid, rp.payment_number AS pno, je.id AS jid, je.journal_number AS jno, je.journal_date AS tgl, SUM(jl.credit) AS ar_credit
    FROM receive_payments rp JOIN journal_entries je ON je.id = rp.journal_id {AR}
    WHERE rp.tenant_id = $1 AND {HIDUP} AND jl.credit > 0
      AND rp.id IN (SELECT payment_id FROM receive_payment_allocations WHERE invoice_id IN (SELECT id FROM ai))
    GROUP BY rp.id, je.id),
bg AS (
    SELECT rpa.invoice_id, pb.*,
           CASE WHEN SUM(rpa.amount_applied) OVER w > 0 THEN ROUND(pb.ar_credit * rpa.amount_applied / SUM(rpa.amount_applied) OVER w, 2)
                ELSE 0 END AS bagian,
           ROW_NUMBER() OVER (PARTITION BY rpa.payment_id ORDER BY rpa.id DESC) AS urut_akhir
    FROM receive_payment_allocations rpa JOIN pb ON pb.pid = rpa.payment_id
    WINDOW w AS (PARTITION BY rpa.payment_id)),
bx AS (SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.pid) AS jumlah FROM bg b)
SELECT 1 AS urut, je.source_type AS st, je.journal_date AS tgl, je.id AS jid, je.journal_number AS jno, 'sales_invoice' AS tipe, ai.id AS did,
       ai.invoice_number AS dno, NULL::text AS ket, SUM(jl.debit) AS debit, 0::numeric AS kredit
FROM ai JOIN journal_entries je ON je.source_id = ai.id AND je.source_type = 'INVOICE' {AR}
WHERE {HIDUP} AND jl.debit > 0 GROUP BY je.id, ai.id, ai.invoice_number
UNION ALL
SELECT 2, 'RECEIVE_PAYMENT', bx.tgl, bx.jid, bx.jno, 'receive_payment', bx.pid, bx.pno, ai.invoice_number, 0,
       bx.bagian + CASE WHEN bx.urut_akhir = 1 AND bx.jumlah > 0 THEN bx.ar_credit - bx.jumlah ELSE 0 END
FROM bx JOIN ai ON ai.id = bx.invoice_id
UNION ALL
SELECT 3, je.source_type, je.journal_date, je.id, je.journal_number, 'credit_note', cn.id, cn.credit_note_number, ai.invoice_number, 0, SUM(jl.credit)
FROM credit_notes cn JOIN ai ON ai.id = cn.original_invoice_id JOIN journal_entries je ON je.source_id = cn.id AND je.source_type = 'CREDIT_NOTE' {AR}
WHERE cn.tenant_id = $1 AND {HIDUP} AND jl.credit > 0 GROUP BY je.id, cn.id, ai.invoice_number
UNION ALL
SELECT 4, je.source_type, je.journal_date, je.id, je.journal_number, 'deposit_application', d.id, d.deposit_number, ai.invoice_number, 0, SUM(jl.credit)
FROM customer_deposit_applications cda JOIN ai ON ai.id = cda.invoice_id
JOIN journal_entries je ON je.id = cda.journal_id AND je.source_type = 'DEPOSIT_APPLICATION' {AR}
LEFT JOIN customer_deposits d ON d.id = cda.deposit_id AND d.tenant_id = $1
WHERE cda.tenant_id = $1 AND cda.status = 'active' AND {HIDUP} AND jl.credit > 0 GROUP BY je.id, cda.id, d.id, ai.invoice_number
UNION ALL
SELECT 5, je.source_type, je.journal_date, je.id, je.journal_number, 'sales_invoice', ai.id, ai.invoice_number, NULL, SUM(jl.debit), 0
FROM ai JOIN journal_entries je ON je.source_id = ai.id AND je.source_type = 'RECEIVE_PAYMENT_UNAPPLY' {AR}
WHERE {HIDUP} AND jl.debit > 0 GROUP BY je.id, ai.id, ai.invoice_number
UNION ALL
SELECT 6, je.source_type, je.journal_date, je.id, je.journal_number, 'credit_note', cn.id, cn.credit_note_number, NULL, SUM(jl.debit), SUM(jl.credit)
FROM credit_notes cn JOIN journal_entries je ON je.source_id = cn.id AND je.source_type IN ('CREDIT_NOTE', 'CREDIT_NOTE_REFUND') {AR}
WHERE cn.tenant_id = $1 AND cn.customer_id = $2 AND cn.original_invoice_id IS NULL AND {HIDUP} AND je.reversal_of_id IS NULL
GROUP BY je.id, cn.id
"""

# jurnal milik pelanggan yang DIBATALKAN (reversed_by_id terisi) -- tampil berpasangan dengan pembaliknya bila tanpa_batal=False
MILIK_AR = """(
    (je.source_type IN ('INVOICE', 'RECEIVE_PAYMENT_UNAPPLY')
     AND je.source_id IN (SELECT id FROM sales_invoices WHERE tenant_id = $1 AND customer_id = $2))
 OR (je.source_type IN ('CREDIT_NOTE', 'CREDIT_NOTE_REFUND') AND je.source_id IN (SELECT id FROM credit_notes WHERE tenant_id = $1 AND customer_id = $2))
 OR je.id IN (SELECT journal_id FROM receive_payments WHERE tenant_id = $1 AND customer_id = $2)
 OR je.id IN (SELECT cda.journal_id FROM customer_deposit_applications cda JOIN sales_invoices si ON si.id = cda.invoice_id AND si.tenant_id = $1
              WHERE cda.tenant_id = $1 AND si.customer_id = $2))"""

# ---------------------------------------------------------------- VENDOR (cermin compute_ap_outstanding)
AP = _akun("PAYABLE")
SQL_AP = f"""
WITH ab AS (SELECT id, invoice_number FROM bills WHERE tenant_id = $1 AND vendor_id = $2 AND status_v2 NOT IN ('draft', 'void')),
aktif AS (SELECT id FROM bills WHERE tenant_id = $1 AND status_v2 NOT IN ('draft', 'void')),
pb AS (  -- pd_bayar: debit hutang jurnal pembayaran keluar (pembayaran vendor ini + yang dialokasi ke tagihan vendor ini)
    SELECT p.id AS pid, p.payment_number AS pno, p.vendor_id AS pv, je.id AS jid, je.journal_number AS jno, je.journal_date AS tgl,
           SUM(jl.debit) AS ap_debit
    FROM bill_payments_v2 p JOIN journal_entries je ON je.id = p.journal_id {AP}
    WHERE p.tenant_id = $1 AND {HIDUP} AND jl.debit > 0
      AND (p.vendor_id = $2 OR p.id IN (SELECT payment_id FROM bill_payment_allocations WHERE bill_id IN (SELECT id FROM ab)))
    GROUP BY p.id, je.id),
bg AS (
    SELECT bpa.bill_id, pb.*,
           CASE WHEN SUM(bpa.amount_applied) OVER w > 0 THEN ROUND(pb.ap_debit * bpa.amount_applied / SUM(bpa.amount_applied) OVER w, 2)
                ELSE 0 END AS bagian,
           ROW_NUMBER() OVER (PARTITION BY bpa.payment_id ORDER BY bpa.id DESC) AS urut_akhir
    FROM bill_payment_allocations bpa JOIN pb ON pb.pid = bpa.payment_id
    WINDOW w AS (PARTITION BY bpa.payment_id)),
bx AS (SELECT b.*, b.bagian + CASE WHEN b.urut_akhir = 1 AND SUM(b.bagian) OVER (PARTITION BY b.pid) > 0
                                   THEN b.ap_debit - SUM(b.bagian) OVER (PARTITION BY b.pid) ELSE 0 END AS nilai FROM bg b)
SELECT 1 AS urut, je.source_type AS st, je.journal_date AS tgl, je.id AS jid, je.journal_number AS jno, 'bill' AS tipe, ab.id AS did,
       ab.invoice_number AS dno, NULL::text AS ket, 0::numeric AS debit, SUM(jl.credit) AS kredit
FROM ab JOIN journal_entries je ON je.source_id = ab.id AND je.source_type = 'BILL' {AP}
WHERE {HIDUP} AND jl.credit > 0 GROUP BY je.id, ab.id, ab.invoice_number
UNION ALL  -- payment_debits: bagian ke tagihan aktif vendor ini
SELECT 2, 'BILL_PAYMENT', bx.tgl, bx.jid, bx.jno, 'bill_payment', bx.pid, bx.pno, ab.invoice_number, bx.nilai, 0
FROM bx JOIN ab ON ab.id = bx.bill_id
UNION ALL  -- yatim_rows: pembayaran vendor ini ke tagihan TAK aktif + pembayaran tanpa alokasi
SELECT 2, 'BILL_PAYMENT', bx.tgl, bx.jid, bx.jno, 'bill_payment', bx.pid, bx.pno, NULL, bx.nilai, 0
FROM bx WHERE bx.pv = $2 AND bx.bill_id NOT IN (SELECT id FROM aktif)
UNION ALL
SELECT 2, 'BILL_PAYMENT', pb.tgl, pb.jid, pb.jno, 'bill_payment', pb.pid, pb.pno, NULL, pb.ap_debit, 0
FROM pb WHERE pb.pv = $2 AND NOT EXISTS (SELECT 1 FROM bill_payment_allocations a0 WHERE a0.payment_id = pb.pid)
UNION ALL
SELECT 3, je.source_type, je.journal_date, je.id, je.journal_number, 'vendor_credit', vc.id, vc.credit_number, ab.invoice_number, SUM(jl.debit), 0
FROM vendor_credits vc JOIN ab ON ab.id = vc.original_bill_id JOIN journal_entries je ON je.source_id = vc.id AND je.source_type = 'VENDOR_CREDIT' {AP}
WHERE vc.tenant_id = $1 AND {HIDUP} AND jl.debit > 0 GROUP BY je.id, vc.id, ab.invoice_number
UNION ALL
SELECT 4, je.source_type, je.journal_date, je.id, je.journal_number, 'deposit_application', d.id, d.deposit_number, ab.invoice_number, SUM(jl.debit), 0
FROM vendor_deposit_applications vda JOIN ab ON ab.id = vda.bill_id
JOIN journal_entries je ON je.id = vda.journal_id AND je.source_type = 'DEPOSIT_APPLICATION' {AP}
LEFT JOIN vendor_deposits d ON d.id = vda.vendor_deposit_id AND d.tenant_id = $1
WHERE {HIDUP} AND jl.debit > 0 GROUP BY je.id, vda.id, d.id, ab.invoice_number
UNION ALL
SELECT 5, je.source_type, je.journal_date, je.id, je.journal_number, 'vendor_credit', vc.id, vc.credit_number, NULL, SUM(jl.debit), SUM(jl.credit)
FROM vendor_credits vc JOIN journal_entries je ON je.source_id = vc.id AND je.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND') {AP}
WHERE vc.tenant_id = $1 AND vc.vendor_id = $2 AND vc.original_bill_id IS NULL AND {HIDUP} AND je.reversal_of_id IS NULL
GROUP BY je.id, vc.id
UNION ALL
SELECT 6, je.source_type, je.journal_date, je.id, je.journal_number, 'opening_balance', $2::uuid, NULL, NULL, SUM(jl.debit), SUM(jl.credit)
FROM journal_entries je {AP}
WHERE {HIDUP} AND je.source_type = 'VENDOR_OPENING_BALANCE' AND je.source_id = $2 AND je.reversal_of_id IS NULL
GROUP BY je.id
"""

MILIK_AP = """(
    (je.source_type = 'BILL' AND je.source_id IN (SELECT id FROM bills WHERE tenant_id = $1 AND vendor_id = $2))
 OR (je.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND') AND je.source_id IN (SELECT id FROM vendor_credits WHERE tenant_id = $1 AND vendor_id = $2))
 OR je.id IN (SELECT journal_id FROM bill_payments_v2 WHERE tenant_id = $1 AND vendor_id = $2)
 OR je.id IN (SELECT vda.journal_id FROM vendor_deposit_applications vda JOIN bills b ON b.id = vda.bill_id AND b.tenant_id = $1 WHERE b.vendor_id = $2)
 OR (je.source_type = 'VENDOR_OPENING_BALANCE' AND je.source_id = $2))"""


def sql_batal(tipe_akun: str, milik: str) -> str:
    """Jurnal pihak yang dibatalkan (urut 9) + pembaliknya (urut 10): sisi akun apa adanya, neto nol per pasangan."""
    akun = _akun(tipe_akun)
    return f"""
SELECT 9 AS urut, je.source_type AS st, je.journal_date AS tgl, je.id AS jid, je.journal_number AS jno, NULL::text AS tipe, je.source_id AS did,
       NULL::text AS dno, NULL::text AS ket, SUM(jl.debit) AS debit, SUM(jl.credit) AS kredit
FROM journal_entries je {akun}
WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.reversed_by_id IS NOT NULL AND {milik}
GROUP BY je.id
UNION ALL
SELECT 10, je.source_type, rv.journal_date, rv.id, rv.journal_number, NULL, je.source_id, NULL, je.journal_number, SUM(jl.debit), SUM(jl.credit)
FROM journal_entries je JOIN journal_entries rv ON rv.id = je.reversed_by_id AND rv.tenant_id = $1
JOIN journal_lines jl ON jl.journal_id = rv.id JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = '{tipe_akun}'
WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.reversed_by_id IS NOT NULL AND {milik}
GROUP BY je.id, rv.id"""


JENIS = {
    "customer": {"tabel": "customers", "nama": "nama", "sql": SQL_AR, "batal": sql_batal("RECEIVABLE", MILIK_AR), "tanda": 1,
                 "akun_text": "Piutang usaha", "galat": "Pelanggan tidak ditemukan."},
    "vendor": {"tabel": "vendors", "nama": "name", "sql": SQL_AP, "batal": sql_batal("PAYABLE", MILIK_AP), "tanda": -1,
               "akun_text": "Hutang usaha", "galat": "Vendor tidak ditemukan."},
}

# teks jenis per (urut, source_type) -- teks dari server; FE tak memetakan
TEKS = {
    "INVOICE": "Faktur penjualan", "RECEIVE_PAYMENT": "Pembayaran masuk", "CREDIT_NOTE": "Nota kredit",
    "CREDIT_NOTE_REFUND": "Pengembalian dana nota kredit", "DEPOSIT_APPLICATION": "Pemakaian uang muka",
    "RECEIVE_PAYMENT_UNAPPLY": "Lepas pembayaran", "BILL": "Faktur pembelian", "BILL_PAYMENT": "Pembayaran keluar",
    "VENDOR_CREDIT": "Nota debit", "VENDOR_CREDIT_REFUND": "Pengembalian dana nota debit", "VENDOR_OPENING_BALANCE": "Saldo awal hutang",
}
TIPE_BATAL = {"INVOICE": "sales_invoice", "RECEIVE_PAYMENT_UNAPPLY": "sales_invoice", "CREDIT_NOTE": "credit_note",
              "RECEIVE_PAYMENT": "receive_payment", "BILL_PAYMENT": "bill_payment",
              "CREDIT_NOTE_REFUND": "credit_note", "BILL": "bill", "VENDOR_CREDIT": "vendor_credit", "VENDOR_CREDIT_REFUND": "vendor_credit",
              "VENDOR_OPENING_BALANCE": "opening_balance"}
MODUL_DOK = {"sales_invoice": "sales_invoice", "receive_payment": "receive_payment", "credit_note": "credit_note",
             "deposit_application": {"customer": "customer_deposit", "vendor": "vendor_deposit"}, "bill": "purchase_invoice", "bill_payment": "send_payment", "vendor_credit": "debit_note",
             "opening_balance": None}


def modul_dok(tipe: Optional[str], jenis: str) -> Optional[str]:
    """Modul izin BACA dokumen baris (pola omitted B4); None = tak ada dokumen bermodul (saldo awal)."""
    m = MODUL_DOK.get(tipe)
    return m.get(jenis) if isinstance(m, dict) else m


def jenis_text(urut: int, st: Optional[str]) -> str:
    """Teks jenis baris dari server. 9 = jurnal yang dibatalkan, 10 = pembaliknya."""
    teks = TEKS.get(st) or (st or "Jurnal").replace("_", " ").capitalize()
    return f"Pembatalan {teks.lower()}" if urut == 10 else f"{teks} (dibatalkan)" if urut == 9 else teks


def keterangan(urut: int, ket: Optional[str]) -> Optional[str]:
    """`ket` = nomor faktur/tagihan tujuan (komponen per alokasi) atau, untuk pembalik, nomor jurnal yang dibatalkan."""
    if not ket:
        return None
    return f"membatalkan {ket}" if urut == 10 else f"untuk {ket}"




def _waktu(r) -> tuple:
    """Kunci waktu buat: jurnal tanpa created_at (data lama) paling awal dalam tanggalnya; tak membandingkan None dengan waktu."""
    return (r.get("dibuat") is not None, r.get("dibuat") or 0)

def susun(baris: list, jenis: str, dari, skip: int, limit: int) -> dict:
    """Urutkan (tanggal, WAKTU BUAT jurnal, urut komponen, nomor), saldo berjalan atas SELURUH urutan, saldo_awal = baris sebelum `dari`.
    Temuan B (TESTER nyawit 9 Nov): dulu nomor jurnal sebelum waktu -> "BP-0013" (11:37) tampil sebelum "BILL-0016" (10:43) di hari
    yang sama dan saldo berjalan sempat negatif. `dibuat` = journal_entries.created_at (kronologis); saldo akhir tak berubah."""
    tanda = JENIS[jenis]["tanda"]
    baris = sorted(baris, key=lambda r: (r["tgl"], _waktu(r), r["urut"], r["jno"] or "", r["dno"] or ""))
    saldo, awal, isi, td, tk = NOL, NOL, [], NOL, NOL
    for r in baris:
        d, k = Decimal(str(r["debit"] or 0)), Decimal(str(r["kredit"] or 0))
        saldo += tanda * (d - k)
        if dari is not None and r["tgl"] < dari:
            awal = saldo
            continue
        td, tk = td + d, tk + k
        isi.append({**r, "debit": d, "kredit": k, "saldo": saldo})
    return {"saldo_awal": awal, "saldo_akhir": saldo, "total_debit": td, "total_kredit": tk, "total": len(isi),
            "baris": isi[skip:skip + limit]}


async def mutasi(conn, tid: str, jenis: str, pihak_id, *, dari=None, sampai=None, tanpa_batal: bool = True, skip: int = 0,
                 limit: int = 50, boleh=None) -> Optional[dict]:
    """-> data kontrak GET /{customers|vendors}/{id}/mutasi, atau None bila kontak tak ada di tenant ini.
    boleh(modul) async -> bool: pola omitted B4 (nomor dokumen modul tanpa izin BACA = null; angka tetap)."""
    from ..schemas.uang_json import _uang_json as uang
    j = JENIS[jenis]
    pihak = await conn.fetchrow(f"SELECT id, {j['nama']} AS nama FROM {j['tabel']} WHERE id = $1 AND tenant_id = $2", pihak_id, tid)
    if not pihak:
        return None
    sql = j["sql"] if tanpa_batal else f"{j['sql']}\nUNION ALL\n{j['batal']}"
    baris = [dict(r) for r in await conn.fetch(f"SELECT {KOLOM} FROM ({sql}) m", tid, pihak_id)]
    jids = list({r["jid"] for r in baris})
    dibuat = {r["id"]: r["created_at"] for r in await conn.fetch(
        "SELECT id, created_at FROM journal_entries WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tid, jids)} if jids else {}
    for r in baris:
        r["dibuat"] = dibuat.get(r["jid"])
    if sampai is not None:
        baris = [r for r in baris if r["tgl"] <= sampai]
    hasil = susun(baris, jenis, dari, skip, limit)
    izin, omitted = {}, []
    for m in {modul_dok(r["tipe"] or TIPE_BATAL.get(r["st"]), jenis) for r in hasil["baris"]} - {None}:
        izin[m] = True if boleh is None else await boleh(m)
        if not izin[m]:
            omitted.append(m)

    def satu(r):
        tipe = r["tipe"] or TIPE_BATAL.get(r["st"])
        tampak = izin.get(modul_dok(tipe, jenis), True)
        ket = keterangan(r["urut"], r["ket"])
        return {"id": f"{r['jid']}:{r['urut']}:{r['did'] or ''}", "tanggal": r["tgl"].isoformat(),
                "jurnal": {"id": str(r["jid"]), "nomor": r["jno"]},
                "dokumen": {"tipe": tipe, "id": str(r["did"]) if r["did"] else None, "nomor": r["dno"] if tampak else None},
                "jenis_text": jenis_text(r["urut"], r["st"]), "keterangan": ket if tampak else None,
                "debit": uang(r["debit"]), "kredit": uang(r["kredit"]), "saldo": uang(r["saldo"]), "batal": r["urut"] >= 9}
    return {"pihak": {"id": str(pihak["id"]), "nama": pihak["nama"], "jenis": jenis}, "akun_text": j["akun_text"],
            "dari": dari.isoformat() if dari else None, "sampai": sampai.isoformat() if sampai else None, "tanpa_batal": tanpa_batal,
            "saldo_awal": uang(hasil["saldo_awal"]), "total_debit": uang(hasil["total_debit"]), "total_kredit": uang(hasil["total_kredit"]),
            "saldo_akhir": uang(hasil["saldo_akhir"]), "total": hasil["total"], "skip": skip, "limit": limit,
            "baris": [satu(r) for r in hasil["baris"]], "omitted": omitted}
