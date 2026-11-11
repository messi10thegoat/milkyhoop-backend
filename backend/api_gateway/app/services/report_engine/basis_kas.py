"""BASIS KAS laba-rugi per AKUN (laporan-fix-2; putusan pemilik LANGSUNG 10 Nov + arah MASTER 12 Nov; pola QuickBooks cash basis).

SATU sumber untuk Laba Rugi PSAK basis kas DAN dasbor `laba_rugi_kas` (kisi dua sumber: dulu PSAK basis kas menyaring "jurnal yang menyentuh kas" =
pendapatan 0 sedangkan dasbor memakai alokasi pembayaran; dua definisi, dua angka). Angka dari JURNAL EFEKTIF + ALOKASI pembayaran (Law 1/16), bukan
SUM tabel pembungkus. Diakui saat DIBAYAR, proporsional, tanpa PPN:
  pendapatan  Σ alokasi bayar faktur (penerimaan posted aktif + uang muka bertanggal di periode) × (kredit pendapatan jurnal efektif faktur ÷ total faktur)
              per AKUN pendapatan (REVENUE + OTHER_INCOME);
  HPP         Σ alokasi bayar faktur × (debit-neto akun COGS jurnal pengiriman/penyelesaian efektif faktur ÷ total faktur), per akun COGS
              (INVOICE_FULFILLMENT, DELIVERY_INVOICED; source_id jurnal = faktur -- terukur pemilik 12 Nov: nol TAK_DIKENAL);
  beban       beban jurnal efektif ber-baris kas (dibayar kas langsung) + Σ alokasi Pembayaran Keluar × (debit-neto beban jurnal efektif tagihan ÷ total tagihan)
              per akun; pembelian persediaan (aset) bukan beban di basis kas.
Belum: nota kredit/retur (CREDIT_NOTE, CREDIT_NOTE_COGS) -- tak ikut definisi dasbor 22 Okt; arah MASTER "proporsional dengan cara sama" menunggu aturan
(tiket). Neraca TIDAK memakai ini (selalu akrual).
Keluaran: {tipe_akun: [{account_id, account_code, account_name, balance}]} balance = debit - kredit (konvensi compute_balance_detail: pendapatan NEGATIF),
Decimal dibulatkan ke sen per akun. Baca saja; filter tenant eksplisit."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

NOL = Decimal(0)
SEN = Decimal("0.01")
TIPE_PENDAPATAN = ("REVENUE", "OTHER_INCOME")
TIPE_BEBAN = ("COGS", "EXPENSE", "OTHER_EXPENSE")
SUMBER_HPP = ("INVOICE_FULFILLMENT", "DELIVERY_INVOICED")

# alokasi pembayaran faktur: penerimaan posted aktif + uang muka ber-jurnal tak void yang DITERAPKAN (bertanggal uang muka)
_BAYAR = """
    bayar AS (
        SELECT a.invoice_id, a.amount_applied, rp.payment_date AS tgl
        FROM receive_payment_allocations a JOIN receive_payments rp ON rp.id = a.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
        WHERE a.status = 'active'
        UNION ALL
        SELECT cda.invoice_id, cda.amount_applied, cd.deposit_date
        FROM customer_deposit_applications cda
        JOIN customer_deposits cd ON cd.id = cda.deposit_id AND cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
        WHERE cda.tenant_id = $1 AND cda.status = 'active'
    )
"""


def _per_faktur(tipe_sql: str, sumber_sql: str, ekspresi: str) -> str:
    """Bagian faktur per akun (jurnal efektif) × alokasi bayar / total faktur, di periode $2..$3, per akun."""
    return f"""
    WITH bagian AS (
        SELECT je.source_id AS inv, jl.account_id, SUM({ekspresi}) AS r
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
          AND c.account_type IN ({tipe_sql}) AND je.source_type IN ({sumber_sql})
        GROUP BY je.source_id, jl.account_id
    ),
    {_BAYAR}
    SELECT bagian.account_id, SUM(b.amount_applied * bagian.r / NULLIF(si.total_amount, 0)) AS jumlah
    FROM bayar b JOIN sales_invoices si ON si.id = b.invoice_id AND si.tenant_id = $1 JOIN bagian ON bagian.inv = si.id
    WHERE b.tgl BETWEEN $2 AND $3
    GROUP BY bagian.account_id
"""


def _sql_in(xs) -> str:
    return ", ".join(f"'{x}'" for x in xs)


SQL_PENDAPATAN = _per_faktur(_sql_in(TIPE_PENDAPATAN), "'INVOICE_REVENUE', 'INVOICE'", "jl.credit - jl.debit")
SQL_HPP_FAKTUR = _per_faktur("'COGS'", _sql_in(SUMBER_HPP), "jl.debit - jl.credit")
SQL_BEBAN_LANGSUNG = f"""
    SELECT jl.account_id, SUM(jl.debit - jl.credit) AS jumlah
    FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
      AND c.account_type IN ({_sql_in(TIPE_BEBAN)}) AND je.journal_date BETWEEN $2 AND $3
      AND EXISTS (SELECT 1 FROM journal_lines k JOIN chart_of_accounts kc ON kc.id = k.account_id WHERE k.journal_id = je.id AND kc.is_cash)
    GROUP BY jl.account_id
"""
SQL_BEBAN_TAGIHAN = f"""
    WITH beban_tagihan AS (
        SELECT je.source_id AS bill, jl.account_id, SUM(jl.debit - jl.credit) AS beban
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts c ON c.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND je.source_type = 'BILL'
          AND c.account_type IN ({_sql_in(TIPE_BEBAN)})
        GROUP BY je.source_id, jl.account_id
    )
    SELECT bt.account_id, SUM(a.amount_applied * bt.beban / NULLIF(COALESCE(b.grand_total, b.amount), 0)) AS jumlah
    FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id AND p.tenant_id = $1 AND p.status = 'posted'
    JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $1 JOIN beban_tagihan bt ON bt.bill = b.id
    WHERE p.payment_date BETWEEN $2 AND $3
    GROUP BY bt.account_id
"""
SQL_AKUN = "SELECT id, account_code, name, account_type FROM chart_of_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[])"


def d(v) -> Decimal:
    return Decimal(str(v or 0))


async def detail(conn, tid: str, mulai: date, akhir: date) -> dict:
    """{tipe_akun: [baris per akun]} basis kas (lihat docstring modul). balance = debit - kredit; nol dibuang; urut kode akun."""
    saldo = {}  # account_id -> debit - kredit (Decimal penuh)
    for sql, tanda in ((SQL_PENDAPATAN, -1), (SQL_HPP_FAKTUR, 1), (SQL_BEBAN_LANGSUNG, 1), (SQL_BEBAN_TAGIHAN, 1)):
        for r in await conn.fetch(sql, tid, mulai, akhir):
            saldo[r["account_id"]] = saldo.get(r["account_id"], NOL) + tanda * d(r["jumlah"])
    hasil = {t: [] for t in TIPE_PENDAPATAN + TIPE_BEBAN}
    if not saldo:
        return hasil
    for a in await conn.fetch(SQL_AKUN, tid, list(saldo)):
        x = saldo[a["id"]].quantize(SEN, rounding=ROUND_HALF_UP)
        if a["account_type"] in hasil and x != 0:
            hasil[a["account_type"]].append({"account_id": a["id"], "account_code": a["account_code"], "account_name": a["name"], "balance": x})
    for baris in hasil.values():
        baris.sort(key=lambda z: z["account_code"])
    return hasil


def ringkas(det: dict) -> dict:
    """{pendapatan, beban, laba} dari detail (pendapatan = -Σ saldo REVENUE+OTHER_INCOME; beban = Σ saldo COGS+EXPENSE+OTHER_EXPENSE)."""
    pendapatan = -sum((z["balance"] for t in TIPE_PENDAPATAN for z in det[t]), NOL)
    beban = sum((z["balance"] for t in TIPE_BEBAN for z in det[t]), NOL)
    return {"pendapatan": pendapatan, "beban": beban, "laba": pendapatan - beban}
