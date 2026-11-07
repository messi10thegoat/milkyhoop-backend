"""Kas & Bank ringkas: ui-pusat U6 fase 1, BACA saja (putusan pemilik LANGSUNG 6 Nov 2026: "jangan dummy").

B1 ringkasan: saldo per akun = SUM(debit) - SUM(credit) journal_lines POSTED pada coa_id akun (Law 1/16; BUKAN current_balance).
total_saldo = akun aktif selain kartu kredit; kartu kredit (CoA LIABILITY) dipisah sebagai total_kewajiban_kartu (positif = terutang).
B2 mutasi terakhir lintas akun = baris jurnal POSTED pada CoA kas/bank. SEMUA baris POSTED ikut: jurnal asli yang dibatalkan dan
pembaliknya ditandai lewat status, tidak disembunyikan, supaya jumlah mutasi selalu sama dengan saldo (kaos punya pembalik POSTED
yang jurnal aslinya VOID; menyembunyikan pembalik akan membuat daftar tak cocok dengan saldo). Tenant eksplisit (Law 24).
B2b (MASTER 7 Nov, halaman Kas & Bank standar SO): `nomor_dokumen` + `pihak` {jenis pelanggan|vendor|null, id, nama} dari DOKUMEN SUMBER
(je.source_id -> satu tabel per source_type; diukur 7 Nov: tiap source_type kas/bank cocok ke tepat satu tabel), bukan diurai dari
description. Mutasi manual = pihak teks bebas payee_payer (jenis/id null); transfer/saldo awal = tanpa pihak; tak dikenal -> null."""
from decimal import Decimal

TIPE_KARTU = "credit_card"
SEN = Decimal("0.01")

SQL_RINGKASAN = """
SELECT ba.id, ba.account_name, ba.account_type, ba.bank_name, ba.account_number, ba.currency,
       COALESCE(j.saldo, 0) AS saldo, j.mutasi_terakhir, COALESCE(j.jumlah, 0) AS jumlah
FROM bank_accounts ba
LEFT JOIN LATERAL (
    SELECT SUM(jl.debit) - SUM(jl.credit) AS saldo, MAX(je.journal_date) AS mutasi_terakhir, COUNT(*) AS jumlah
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    WHERE je.tenant_id = ba.tenant_id AND je.status = 'POSTED' AND jl.account_id = ba.coa_id
) j ON true
WHERE ba.tenant_id = $1 AND ba.is_active = true
ORDER BY ba.account_type, ba.account_name"""

SQL_MUTASI = """
SELECT jl.id, je.id AS journal_id, je.journal_number, je.journal_date, je.created_at, je.source_type, je.source_id,
       je.reversal_of_id, je.reversed_by_id, ba.id AS bank_account_id, ba.account_name,
       jl.debit - jl.credit AS net, COALESCE(NULLIF(btrim(trf.notes), ''), NULLIF(jl.memo, ''), je.description) AS keterangan,
       dok.nomor_dokumen, dok.pihak_jenis, dok.pihak_id, dok.pihak_nama
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN bank_accounts ba ON ba.coa_id = jl.account_id AND ba.tenant_id = je.tenant_id
LEFT JOIN bank_transfers trf ON je.source_type = 'BANK_TRANSFER' AND trf.id = je.source_id AND trf.tenant_id = je.tenant_id
LEFT JOIN LATERAL (
    SELECT d.nomor_dokumen, d.pihak_jenis, d.pihak_id, d.pihak_nama FROM (
        SELECT x.deposit_number AS nomor_dokumen, 'pelanggan' AS pihak_jenis, c.id AS pihak_id, c.nama AS pihak_nama
        FROM customer_deposits x LEFT JOIN customers c ON c.id = x.customer_id AND c.tenant_id = x.tenant_id
        WHERE je.source_type IN ('CUSTOMER_DEPOSIT', 'DEPOSIT_REFUND') AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT x.payment_number, 'pelanggan', c.id, c.nama
        FROM receive_payments x LEFT JOIN customers c ON c.id = x.customer_id AND c.tenant_id = x.tenant_id
        WHERE je.source_type IN ('RECEIVE_PAYMENT', 'PAYMENT_RECEIVED') AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT x.credit_note_number, 'pelanggan', c.id, c.nama
        FROM credit_notes x LEFT JOIN customers c ON c.id = x.customer_id AND c.tenant_id = x.tenant_id
        WHERE je.source_type = 'CREDIT_NOTE_REFUND' AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT x.payment_number, 'vendor', v.id, v.name
        FROM bill_payments_v2 x LEFT JOIN vendors v ON v.id = x.vendor_id AND v.tenant_id = x.tenant_id
        WHERE je.source_type IN ('BILL_PAYMENT', 'PAYMENT_BILL', 'PAYMENT_MADE') AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT x.deposit_number, 'vendor', v.id, v.name
        FROM vendor_deposits x LEFT JOIN vendors v ON v.id = x.vendor_id AND v.tenant_id = x.tenant_id
        WHERE je.source_type IN ('VENDOR_DEPOSIT', 'DEPOSIT_REFUND') AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT x.expense_number, 'vendor', v.id, v.name
        FROM expenses x LEFT JOIN vendors v ON v.id = x.vendor_id AND v.tenant_id = x.tenant_id
        WHERE je.source_type IN ('EXPENSE', 'EXPENSE_REVERSAL') AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT x.transaction_number, NULL, NULL, NULLIF(btrim(x.payee_payer), '')
        FROM bank_transactions x WHERE je.source_type = 'BANK_TRANSACTION' AND x.id = je.source_id AND x.tenant_id = je.tenant_id
        UNION ALL
        SELECT trf.transfer_number, NULL, NULL, NULL WHERE trf.id IS NOT NULL
    ) d LIMIT 1
) dok ON true
WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND ba.is_active = true
ORDER BY je.journal_date DESC, je.created_at DESC, jl.id DESC
LIMIT $2"""

LABEL_SUMBER = {
    "RECEIVE_PAYMENT": "Terima pembayaran", "PAYMENT_RECEIVED": "Terima pembayaran",
    "BILL_PAYMENT": "Bayar tagihan", "PAYMENT_BILL": "Bayar tagihan", "PAYMENT_MADE": "Bayar tagihan",
    "CUSTOMER_DEPOSIT": "Uang muka pelanggan", "VENDOR_DEPOSIT": "Uang muka vendor", "DEPOSIT_REFUND": "Pengembalian uang muka",
    "CREDIT_NOTE_REFUND": "Pengembalian ke pelanggan", "VENDOR_CREDIT_REFUND": "Pengembalian dari vendor",
    "EXPENSE": "Biaya", "BANK_TRANSACTION": "Manual", "BANK_TRANSFER": "Transfer", "RECONCILIATION_ADJUSTMENT": "Penyesuaian",
    "OPENING": "Saldo awal", "OPENING_BALANCE": "Saldo awal", "BANK_OPENING_BALANCE": "Saldo awal",
    "CASH_SALE": "Penjualan tunai", "SALES_RECEIPT": "Penjualan tunai", "POS_SALE": "Penjualan kasir",
    "EMPLOYEE_ADVANCE_GRANT": "Kasbon karyawan", "PAYROLL_PAYMENT": "Gaji", "PAYROLL_BPJS_PAYMENT": "BPJS", "PAYROLL_TAX_PAYMENT": "Pajak gaji",
    "MANUAL": "Jurnal manual",
}
LABEL_LAIN = "Jurnal"


def _rp(x) -> str:
    return str(Decimal(str(x or 0)).quantize(SEN))


def _iso(x):
    return x.isoformat() if x else None


def ringkasan(baris: list, as_of) -> dict:
    """Baris SQL_RINGKASAN -> B1. Uang = Decimal string 2 desimal."""
    akun = [{"id": str(b["id"]), "account_name": b["account_name"], "account_type": b["account_type"], "bank_name": b["bank_name"],
             "account_number": b["account_number"], "currency": b["currency"] or "IDR", "saldo": _rp(b["saldo"]),
             "minus": Decimal(str(b["saldo"] or 0)) < 0, "mutasi_terakhir_at": _iso(b["mutasi_terakhir"]),
             "jumlah_mutasi": int(b["jumlah"] or 0)} for b in baris]
    total = sum((Decimal(str(b["saldo"] or 0)) for b in baris if b["account_type"] != TIPE_KARTU), Decimal(0))
    kartu = -sum((Decimal(str(b["saldo"] or 0)) for b in baris if b["account_type"] == TIPE_KARTU), Decimal(0))
    return {"as_of": _iso(as_of), "total_akun": len(akun), "total_saldo": _rp(total), "total_kewajiban_kartu": _rp(kartu), "akun": akun}


def status_mutasi(b) -> str:
    if b["reversed_by_id"]:
        return "dibatalkan"
    st = b["source_type"] or ""
    if b["reversal_of_id"] or st == "REVERSAL" or st.endswith("_REVERSAL"):
        return "pembatalan"
    return "tercatat"


def label_sumber(st: str | None, pembatalan: bool) -> str:
    """Label server untuk source_type; baris pembatalan (EXPENSE_REVERSAL maupun BILL_PAYMENT ber-reversal_of_id) -> "Pembatalan …"."""
    st = (st or "").removesuffix("_REVERSAL")
    teks = LABEL_SUMBER.get(st, LABEL_LAIN)
    return "Pembatalan " + teks.lower() if pembatalan else teks


def pihak(b) -> dict | None:
    """Pihak dokumen sumber; tanpa nama -> None (FE: kolom kosong). jenis/id null = teks bebas (mutasi manual)."""
    if not b["pihak_nama"]:
        return None
    return {"jenis": b["pihak_jenis"], "id": str(b["pihak_id"]) if b["pihak_id"] else None, "nama": b["pihak_nama"]}


def mutasi(baris: list) -> list:
    """Baris SQL_MUTASI -> B2. arah dari tanda debit-kredit di CoA kas/bank; amount selalu positif."""
    return [{"id": str(b["id"]), "journal_id": str(b["journal_id"]), "nomor_jurnal": b["journal_number"],
             "bank_account_id": str(b["bank_account_id"]), "account_name": b["account_name"],
             "transaction_date": _iso(b["journal_date"]), "created_at": _iso(b["created_at"]),
             "arah": "keluar" if Decimal(str(b["net"])) < 0 else "masuk", "amount": _rp(abs(Decimal(str(b["net"])))),
             "description": b["keterangan"], "sumber": (b["source_type"] or "").lower(), "sumber_teks": label_sumber(b["source_type"], status_mutasi(b) == "pembatalan"),
             "source_id": str(b["source_id"]) if b["source_id"] else None, "status": status_mutasi(b),
             "nomor_dokumen": b["nomor_dokumen"], "pihak": pihak(b)} for b in baris]
