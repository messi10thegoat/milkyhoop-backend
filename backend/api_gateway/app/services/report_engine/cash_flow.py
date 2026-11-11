"""
Arus Kas (Cash Flow Statement) — Metode Langsung (Direct Method). PSAK-compliant.
All data from journal_lines (Law 1).

Logic:
1. Net cash per journal (avoids N:M double-counting)
2. Proportional distribution across counterpart classifications
3. Label mapping from counterpart account type + psak_sub_category

Gelombang 4 L1 (10 Nov 2026): butir digabung per label dalam Decimal (dulu penjumlahan FLOAT; Law 9/25), float hanya di batas keluaran
lama; + baris[] tampil server (report_engine.baris; label kas neto mengikuti TANDA: "diperoleh dari" / "digunakan untuk").
"""
from decimal import Decimal
from datetime import date as date_type, datetime, timedelta
from . import baris as B
from .ledger import compute_balance, _to_date


# Label mapping: (psak_sub_category or account_type) -> display label
# psak_sub_category takes priority over account_type
LABEL_MAP_SUB = {
    "TRADE_RECEIVABLE": "Penerimaan piutang usaha",
    "TRADE_PAYABLE": "Pembayaran utang usaha",
    "INVENTORY": "Pembelian persediaan",
    "PAID_IN_CAPITAL": "Setoran modal",
    "RETAINED_EARNINGS": "Distribusi laba",
    "FIXED_ASSET": "Pembelian/penjualan aset tetap",
    "ACCUM_DEPRECIATION": "Penyusutan aset tetap",
}

LABEL_MAP_TYPE = {
    "REVENUE": "Penerimaan dari pelanggan",
    "COGS": "Pembayaran kas kepada pemasok",
    "EXPENSE": "Pembayaran beban usaha",
    "OTHER_INCOME": "Penerimaan lain-lain",
    "OTHER_EXPENSE": "Pembayaran lain-lain",
    "LIABILITY": "Pembayaran liabilitas",
    "PAYABLE": "Pembayaran utang usaha",
    "ASSET": "Penerimaan/pengeluaran aset",
    "RECEIVABLE": "Penerimaan piutang",
    "EQUITY": "Setoran modal / prive",
}


# (a) Jurnal SALDO AWAL bukan aktivitas: kasnya = KAS AWAL periode (dulu masuk Operasi "Transaksi ekuitas", kas awal 0 -- nyawit 282 jt).
SUMBER_SALDO_AWAL = ["OPENING", "OPENING_BALANCE", "BANK_OPENING_BALANCE", "VENDOR_OPENING_BALANCE"]


def _get_label(account_type, psak_sub_category, account_name=None):
    """Get display label from psak_sub_category (priority) or account_type. (c) ASSET tanpa sub-kategori = NAMA akun (mis. "Uang Muka
    Pembelian"), bukan "Penerimaan/pengeluaran aset" yang menyesatkan. laporan-fix-2 (c): LIABILITY juga = NAMA akun (mis. "Uang Muka Pelanggan"
    = DP diterima, baris SENDIRI): label umum "Pembayaran liabilitas" salah arah untuk uang MASUK dan melebur semua liabilitas jadi satu baris."""
    if psak_sub_category and psak_sub_category in LABEL_MAP_SUB:
        return LABEL_MAP_SUB[psak_sub_category]
    if account_type in ("ASSET", "LIABILITY") and account_name:
        return account_name
    return LABEL_MAP_TYPE.get(account_type, f"Lain-lain ({account_type})")


def _kategori(flow_category, account_type) -> str:
    """(b) Lawan EKUITAS tanpa kategori eksplisit (setoran modal, prive) = PENDANAAN (PSAK 2: transaksi dengan pemilik); lainnya tetap."""
    if flow_category in (None, "NONE", "OPERATING") and account_type == "EQUITY":
        return "FINANCING"
    return flow_category or "OPERATING"


async def generate_cash_flow(conn, tenant_id: str, start: str, end: str) -> dict:
    """
    Direct method cash flow statement with line items.
    Uses proportional distribution to avoid N:M double-counting.
    """
    sql = """
        WITH cash_per_journal AS (
            -- Step 1: Net cash movement per journal (avoids double-counting)
            SELECT jl.journal_id,
                   COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0) AS net_cash
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.status = 'POSTED'
              AND is_effective_journal(je.id)   -- laporan-fix-2: SATU definisi dgn kartu arus kas dasbor (jurnal EFEKTIF; pasangan batal tak jadi aliran)
              AND je.tenant_id = $1
              AND je.journal_date >= $2
              AND je.journal_date <= $3
              AND coa.is_cash = TRUE
              AND je.source_type <> ALL($4::text[])
            GROUP BY jl.journal_id
        ),
        counterpart_weights AS (
            -- Step 2: Each counterpart line's proportional weight within its journal
            SELECT cpj.journal_id,
                   cpj.net_cash,
                   COALESCE(counter_coa.cash_flow_category, 'OPERATING') AS flow_category,
                   counter_coa.account_type,
                   CASE WHEN cpj.net_cash > 0 THEN 1 ELSE -1 END AS arah,   -- masuk / keluar per JURNAL (metode langsung: tak dinetokan lintas arah)
                   counter_coa.psak_sub_category,
                   counter_coa.name AS account_name,
                   ABS(counter_jl.debit - counter_jl.credit) AS line_amount,
                   SUM(ABS(counter_jl.debit - counter_jl.credit))
                       OVER (PARTITION BY cpj.journal_id) AS journal_total
            FROM cash_per_journal cpj
            JOIN journal_lines counter_jl ON counter_jl.journal_id = cpj.journal_id
            JOIN chart_of_accounts counter_coa ON counter_coa.id = counter_jl.account_id
            WHERE counter_coa.is_cash IS NOT TRUE
        )
        -- Step 3: Distribute net_cash proportionally across counterpart classifications
        SELECT flow_category,
               account_type,
               arah,
               psak_sub_category,
               CASE WHEN account_type IN ('ASSET', 'LIABILITY') THEN account_name END AS account_name,
               SUM(net_cash * line_amount / NULLIF(journal_total, 0)) AS net_flow
        FROM counterpart_weights
        GROUP BY flow_category, account_type, arah, psak_sub_category, CASE WHEN account_type IN ('ASSET', 'LIABILITY') THEN account_name END
        HAVING ABS(SUM(net_cash * line_amount / NULLIF(journal_total, 0))) > 0.01
        ORDER BY flow_category, account_type, psak_sub_category
    """
    rows = await conn.fetch(sql, tenant_id, _to_date(start), _to_date(end), SUMBER_SALDO_AWAL)

    # Build items per category
    operating_items = []
    investing_items = []
    financing_items = []
    operating_total = Decimal("0")
    investing_total = Decimal("0")
    financing_total = Decimal("0")

    for r in rows:
        category = _kategori(r["flow_category"], r["account_type"])
        amount = Decimal(str(r["net_flow"]))
        label = _get_label(r["account_type"], r["psak_sub_category"], r["account_name"])

        item = {"label": label, "amount": amount, "arah": int(r["arah"])}  # Decimal; float hanya di keluaran lama

        if category == "OPERATING" or category == "NONE":
            operating_items.append(item)
            operating_total += amount
        elif category == "INVESTING":
            investing_items.append(item)
            investing_total += amount
        elif category == "FINANCING":
            financing_items.append(item)
            financing_total += amount
        else:
            # Unknown category → default to operating
            operating_items.append(item)
            operating_total += amount

    # Merge items with same label (Decimal)
    def merge_items(items):
        """Gabung butir berlabel SAMA DAN arah sama (Decimal). Metode langsung: penerimaan dan pembayaran TIDAK dinetokan; label yang punya dua arah
        tampil dua baris "<label> (masuk)" / "<label> (keluar)" (laporan-fix-2, putusan MASTER 12 Nov); label satu arah tetap apa adanya."""
        merged = {}
        for item in items:
            key = (item["label"], item["arah"])
            if key in merged:
                merged[key]["amount"] += item["amount"]
            else:
                merged[key] = {"label": item["label"], "amount": item["amount"], "arah": item["arah"]}
        dua_arah = {lbl for (lbl, a) in merged if (lbl, 1) in merged and (lbl, -1) in merged}
        for (lbl, a), v in merged.items():
            if lbl in dua_arah:
                v["label"] = f"{lbl} ({'masuk' if a > 0 else 'keluar'})"
        return [v for v in merged.values() if abs(v["amount"]) > Decimal("0.01")]

    def keluar(items):  # bentuk lama: amount float
        return [{"label": v["label"], "amount": float(v["amount"])} for v in items]

    operating_items = merge_items(operating_items)
    investing_items = merge_items(investing_items)
    financing_items = merge_items(financing_items)

    net_change = operating_total + investing_total + financing_total

    # Opening cash = all cash account balances as of day before period start
    day_before = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    cash_start = await compute_balance(conn, tenant_id, {"is_cash": True}, end_date=day_before)
    # (a) kas dari jurnal saldo awal yang bertanggal DI DALAM periode = bagian KAS AWAL (bukan aktivitas)
    cash_start += Decimal(str(await conn.fetchval(
        """SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) FROM journal_lines jl
           JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
           WHERE je.status = 'POSTED' AND je.tenant_id = $1 AND je.journal_date >= $2 AND je.journal_date <= $3
             AND coa.is_cash = TRUE AND je.source_type = ANY($4::text[])""",
        tenant_id, _to_date(start), _to_date(end), SUMBER_SALDO_AWAL) or 0))
    cash_end = await compute_balance(conn, tenant_id, {"is_cash": True}, end_date=end)

    # ---- baris tampil (urutan == layar Arus Kas desktop sebelum L1)
    r = []
    for kunci, items, total in (("operasi", operating_items, operating_total), ("investasi", investing_items, investing_total),
                                ("pendanaan", financing_items, financing_total)):
        r.append(B.judul(kunci))
        r += [B.angka("akun", "butir", v["amount"], 1, label=v["label"]) for v in items]
        r.append(B.angka("total", "kas_neto_" + kunci, total, label=B.kas_neto_label(kunci, total)))
    r.append(B.angka("hasil", "kenaikan_kas", net_change))
    r.append(B.angka("info", "kas_awal", cash_start))
    r.append(B.angka("hasil", "kas_akhir", cash_end))

    return {
        "period": {"start": start, "end": end},
        "operasi": {
            "total": float(operating_total),
            "items": keluar(operating_items),
        },
        "investasi": {
            "total": float(investing_total),
            "items": keluar(investing_items),
        },
        "pendanaan": {
            "total": float(financing_total),
            "items": keluar(financing_items),
        },
        "kenaikan_kas_bersih": float(net_change),
        "kas_awal": float(cash_start),
        "kas_akhir": float(cash_end),
        "_reconciled": abs(cash_start + net_change - cash_end) < Decimal("0.01"),
        "basis_applied": "cash",
        # gelombang 4 L1: tampilan server (aditif)
        "judul": B.JUDUL["arus_kas"],
        "periode_text": B.periode_berakhir(end),
        "periode_rentang_text": B.periode_rentang(start, end),
        "satuan_text": B.SATUAN_TEXT,
        "baris": r,
    }
