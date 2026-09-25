"""Ringkasan Penerimaan Pembayaran per periode — HARI INI / MINGGU INI / BULAN INI.

Latar (25 Sep 2026): kartu FE dulu MENJUMLAH BARIS YANG TERMUAT dengan tanggal KLIEN
(ReceivePaymentListDesktop.tsx:135-137) — angka bergantung pada halaman yang sedang
dimuat dan jam peramban. Sumbernya kini server, satu kueri.

Definisi (tertulis, supaya tak ditebak ulang):
- "Hari ini" = tanggal bisnis tenant (utils.tanggal_tenant.tanggal_dokumen; zona tenant,
  cadangan Asia/Jakarta) — BUKAN CURRENT_DATE/UTC (00:00–07:00 WIB = kemarin menurut UTC).
- Minggu = SENIN s/d MINGGU kalender yang memuat hari ini (penuh; pembayaran bertanggal
  maju di minggu yang sama ikut terhitung).
- Bulan = tanggal 1 s/d tanggal terakhir bulan kalender yang memuat hari ini.
- Pembagi periode = receive_payments.payment_date (tanggal dokumen), bukan created_at.
- Uang = kredit Piutang jurnal POSTED yang belum dibalik (Law 16/29) — sumber SAMA dengan
  total_received. Hanya status 'posted': draf dan 'voided' tak pernah masuk.
- count_* = jumlah pembayaran yang uangnya masuk ke amount_* periode itu.
"""

from __future__ import annotations

from datetime import date, timedelta


def batas_periode(hari_ini: date) -> dict[str, date]:
    """Batas periode inklusif dari tanggal bisnis. Murni (diuji dengan tanggal suntikan)."""
    awal_minggu = hari_ini - timedelta(days=hari_ini.weekday())  # Senin
    awal_bulan = hari_ini.replace(day=1)
    bulan_depan = (awal_bulan + timedelta(days=32)).replace(day=1)
    return {
        "today": hari_ini,
        "week_start": awal_minggu,
        "week_end": awal_minggu + timedelta(days=6),  # Minggu
        "month_start": awal_bulan,
        "month_end": bulan_depan - timedelta(days=1),
    }


def argumen_kueri(tenant_id: str, b: dict[str, date]) -> tuple:
    return (tenant_id, b["today"], b["week_start"], b["week_end"], b["month_start"], b["month_end"])


# Satu kueri: hitungan status (metadata tabel) + uang dari jurnal (Law 16) + periode.
# $1 tenant · $2 hari ini · $3–$4 minggu · $5–$6 bulan.
_CTE_UANG = """
    WITH journal_amounts AS (
        SELECT rp.id as payment_id,
               COALESCE(SUM(jl.credit), 0) as journal_amount
        FROM receive_payments rp
        JOIN journal_entries je ON je.id = rp.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE rp.tenant_id = $1
          AND rp.status = 'posted'
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE'
          AND jl.credit > 0
        GROUP BY rp.id
    )
"""

SQL_RINGKASAN = _CTE_UANG + """
    SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE rp.status = 'draft') as draft_count,
        COUNT(*) FILTER (WHERE rp.status = 'posted') as posted_count,
        COUNT(*) FILTER (WHERE rp.status = 'voided') as voided_count,
        COALESCE(SUM(ja.journal_amount), 0) as total_received,
        COALESCE(SUM(rp.allocated_amount) FILTER (WHERE rp.status = 'posted'), 0) as total_allocated,
        COALESCE(SUM(rp.unapplied_amount) FILTER (WHERE rp.status = 'posted'), 0) as total_unapplied,
        COALESCE(SUM(ja.journal_amount) FILTER (WHERE rp.payment_date = $2::date), 0) as amount_today,
        COUNT(ja.payment_id) FILTER (WHERE rp.payment_date = $2::date) as count_today,
        COALESCE(SUM(ja.journal_amount)
                 FILTER (WHERE rp.payment_date BETWEEN $3::date AND $4::date), 0) as amount_this_week,
        COUNT(ja.payment_id)
            FILTER (WHERE rp.payment_date BETWEEN $3::date AND $4::date) as count_this_week,
        COALESCE(SUM(ja.journal_amount)
                 FILTER (WHERE rp.payment_date BETWEEN $5::date AND $6::date), 0) as amount_this_month,
        COUNT(ja.payment_id)
            FILTER (WHERE rp.payment_date BETWEEN $5::date AND $6::date) as count_this_month
    FROM receive_payments rp
    LEFT JOIN journal_amounts ja ON ja.payment_id = rp.id
    WHERE rp.tenant_id = $1
"""


# Rincian per metode (payment_method) — CTE uang yang SAMA, periode yang SAMA, argumen sama.
# JOIN (bukan LEFT): hanya pembayaran yang uangnya masuk total_received.
# Invarian: Σ by_method[*].amount == total_received; Σ amount_<periode> == amount_<periode>.
SQL_PER_METODE = _CTE_UANG + """
    SELECT
        rp.payment_method as metode,
        COUNT(*) as count,
        COALESCE(SUM(ja.journal_amount), 0) as amount,
        COALESCE(SUM(ja.journal_amount) FILTER (WHERE rp.payment_date = $2::date), 0) as amount_today,
        COUNT(*) FILTER (WHERE rp.payment_date = $2::date) as count_today,
        COALESCE(SUM(ja.journal_amount)
                 FILTER (WHERE rp.payment_date BETWEEN $3::date AND $4::date), 0) as amount_this_week,
        COUNT(*) FILTER (WHERE rp.payment_date BETWEEN $3::date AND $4::date) as count_this_week,
        COALESCE(SUM(ja.journal_amount)
                 FILTER (WHERE rp.payment_date BETWEEN $5::date AND $6::date), 0) as amount_this_month,
        COUNT(*) FILTER (WHERE rp.payment_date BETWEEN $5::date AND $6::date) as count_this_month
    FROM receive_payments rp
    JOIN journal_amounts ja ON ja.payment_id = rp.id
    WHERE rp.tenant_id = $1
    GROUP BY rp.payment_method
"""

_KOSONG_METODE = {"count": 0, "amount": 0.0, "count_today": 0, "amount_today": 0.0,
                  "count_this_week": 0, "amount_this_week": 0.0,
                  "count_this_month": 0, "amount_this_month": 0.0}


def rincian_metode(rows, metode_sah) -> dict:
    """Kunci metode sah selalu ada (nol); nilai di luar kontrak (data lama/NULL) tampil APA ADANYA
    dengan kuncinya sendiri ('unknown' untuk NULL) — tak dilipat ke transfer (angka karangan)."""
    out = {m: dict(_KOSONG_METODE) for m in metode_sah}
    for r in rows:
        k = r["metode"] or "unknown"
        out[k] = {kk: (float(r[kk] or 0) if kk.startswith("amount") else (r[kk] or 0)) for kk in _KOSONG_METODE}
    return out


def medan_periode(row, b: dict[str, date]) -> dict:
    return {
        "amount_today": float(row["amount_today"] or 0),
        "count_today": row["count_today"] or 0,
        "amount_this_week": float(row["amount_this_week"] or 0),
        "count_this_week": row["count_this_week"] or 0,
        "amount_this_month": float(row["amount_this_month"] or 0),
        "count_this_month": row["count_this_month"] or 0,
        "period": {k: v.isoformat() for k, v in b.items()},
    }
