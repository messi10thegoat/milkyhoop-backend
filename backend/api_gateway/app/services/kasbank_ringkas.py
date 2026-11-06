"""Kas & Bank ringkas: ui-pusat U6 fase 1, BACA saja (putusan pemilik LANGSUNG 6 Nov 2026: "jangan dummy").

B1 ringkasan: saldo per akun = SUM(debit) - SUM(credit) journal_lines POSTED pada coa_id akun (Law 1/16; BUKAN current_balance).
total_saldo = akun aktif selain kartu kredit; kartu kredit (CoA LIABILITY) dipisah sebagai total_kewajiban_kartu (positif = terutang).
B2 mutasi terakhir lintas akun = baris jurnal POSTED pada CoA kas/bank. SEMUA baris POSTED ikut: jurnal asli yang dibatalkan dan
pembaliknya ditandai lewat status, tidak disembunyikan, supaya jumlah mutasi selalu sama dengan saldo (kaos punya pembalik POSTED
yang jurnal aslinya VOID; menyembunyikan pembalik akan membuat daftar tak cocok dengan saldo). Tenant eksplisit (Law 24)."""
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
       jl.debit - jl.credit AS net, COALESCE(NULLIF(jl.memo, ''), je.description) AS keterangan
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN bank_accounts ba ON ba.coa_id = jl.account_id AND ba.tenant_id = je.tenant_id
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


def label_sumber(st: str | None) -> str:
    st = st or ""
    if st.endswith("_REVERSAL"):
        return "Pembatalan " + LABEL_SUMBER.get(st[: -len("_REVERSAL")], LABEL_LAIN).lower()
    return LABEL_SUMBER.get(st, LABEL_LAIN)


def mutasi(baris: list) -> list:
    """Baris SQL_MUTASI -> B2. arah dari tanda debit-kredit di CoA kas/bank; amount selalu positif."""
    return [{"id": str(b["id"]), "journal_id": str(b["journal_id"]), "nomor_jurnal": b["journal_number"],
             "bank_account_id": str(b["bank_account_id"]), "account_name": b["account_name"],
             "transaction_date": _iso(b["journal_date"]), "created_at": _iso(b["created_at"]),
             "arah": "keluar" if Decimal(str(b["net"])) < 0 else "masuk", "amount": _rp(abs(Decimal(str(b["net"])))),
             "description": b["keterangan"], "sumber": (b["source_type"] or "").lower(), "sumber_teks": label_sumber(b["source_type"]),
             "source_id": b["source_id"], "status": status_mutasi(b)} for b in baris]
