"""Bangkitkan V245 dari definisi HIDUP compute_ap_outstanding / compute_ar_outstanding.

- ROLLBACK = definisi hidup apa adanya (pg_get_functiondef), bukan tulisan ulang tangan.
- Maju = definisi hidup dengan SATU CTE diganti per fungsi; jangkar blok lama di-assert persis 1x.
- Rumus proporsional: bagian alokasi = ROUND(total sisi AP/AR jurnal pembayaran * applied / Σapplied, 2);
  sisa sen diserap alokasi dgn id terbesar (urutan stabil). Σapplied = 0 -> 0.
  Satu alokasi: applied/Σapplied = 1 -> ROUND(x,2) = x (numeric 2dp) -> identik dgn hari ini.
Keluaran: backend/migrations/V245__alokasi_proporsional_ap_ar.sql + _ROLLBACK.sql
"""
import subprocess
import sys

M = "/root/mh-law2/backend/migrations/"


def defn(nama):
    r = subprocess.run(["docker", "exec", "milkyhoop-dev-postgres-1", "psql", "-U", "postgres", "-d", "milkydb", "-At",
                        "-c", f"SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE proname='{nama}'"],
                       capture_output=True, text=True, check=True)
    t = r.stdout.rstrip("\n")
    if t.count("CREATE OR REPLACE FUNCTION") != 1:
        print(f"GAGAL: {nama} bukan tepat 1 definisi"); sys.exit(1)
    return t


ap = defn("compute_ap_outstanding")
ar = defn("compute_ar_outstanding")

AP_LAMA = """    payment_debits AS (
        SELECT bpa.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM bill_payment_allocations bpa
        JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
        JOIN journal_entries je ON je.id = bpv2.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE bpv2.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY bpa.bill_id
    ),"""

AP_BARU = """    -- V245 (13 Sep 2026): PROPORSIONAL per alokasi. Versi lama menjumlahkan SELURUH debit AP
    -- jurnal pembayaran untuk SETIAP tagihan yang dialokasi -> pembayaran 2.000 ke 2 tagihan
    -- menurunkan outstanding MASING-MASING 2.000 (terukur lewat eksekusi). GL benar; laporan per
    -- tagihan salah. Debit AP jurnal = Σ amount_applied terukur pada lebih bayar/diskon/biaya,
    -- jadi bagian tak teralokasi TIDAK ikut dibagi. Satu alokasi -> identik dgn versi lama.
    pd_bayar AS (
        SELECT bpv2.id AS payment_id, COALESCE(SUM(jl.debit), 0) AS ap_debit
        FROM bill_payments_v2 bpv2
        JOIN journal_entries je ON je.id = bpv2.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE bpv2.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY bpv2.id
    ),
    pd_bagian AS (
        SELECT bpa.bill_id, bpa.payment_id, bpa.id AS alloc_id, pb.ap_debit,
               CASE WHEN SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id) > 0
                    THEN ROUND(pb.ap_debit * bpa.amount_applied
                               / SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id), 2)
                    ELSE 0 END AS bagian,
               ROW_NUMBER() OVER (PARTITION BY bpa.payment_id ORDER BY bpa.id DESC) AS urut_akhir
        FROM bill_payment_allocations bpa
        JOIN pd_bayar pb ON pb.payment_id = bpa.payment_id
    ),
    payment_debits AS (
        SELECT x.bill_id,
               COALESCE(SUM(x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0
                                            THEN x.ap_debit - x.jumlah_bagian ELSE 0 END), 0) AS total_debit
        FROM (SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian FROM pd_bagian b) x
        GROUP BY x.bill_id
    ),"""

AR_LAMA = """        SELECT rpa.invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM receive_payment_allocations rpa
        JOIN receive_payments rp ON rp.id = rpa.payment_id
        JOIN journal_entries je ON je.id = rp.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE rp.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY rpa.invoice_id
"""

AR_BARU = """        -- V245 (13 Sep 2026): PROPORSIONAL per alokasi (kembar AP; lihat compute_ap_outstanding).
        -- Versi lama memberi SELURUH kredit AR jurnal penerimaan ke SETIAP faktur yang dialokasi;
        -- cache amount_paid (dihitung ulang dari fungsi ini) ikut ganda -> faktur "lunas" palsu.
        SELECT x.invoice_id AS inv_id,
               COALESCE(SUM(x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0
                                            THEN x.ar_credit - x.jumlah_bagian ELSE 0 END), 0) AS total_credit
        FROM (
            SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian
            FROM (
                SELECT rpa.invoice_id, rpa.payment_id, pb.ar_credit,
                       CASE WHEN SUM(rpa.amount_applied) OVER (PARTITION BY rpa.payment_id) > 0
                            THEN ROUND(pb.ar_credit * rpa.amount_applied
                                       / SUM(rpa.amount_applied) OVER (PARTITION BY rpa.payment_id), 2)
                            ELSE 0 END AS bagian,
                       ROW_NUMBER() OVER (PARTITION BY rpa.payment_id ORDER BY rpa.id DESC) AS urut_akhir
                FROM receive_payment_allocations rpa
                JOIN (
                    SELECT rp.id AS payment_id, COALESCE(SUM(jl.credit), 0) AS ar_credit
                    FROM receive_payments rp
                    JOIN journal_entries je ON je.id = rp.journal_id
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE rp.tenant_id = p_tenant_id
                      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                      AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
                    GROUP BY rp.id
                ) pb ON pb.payment_id = rpa.payment_id
            ) b
        ) x
        GROUP BY x.invoice_id
"""

for nama, teks, lama in (("ap", ap, AP_LAMA), ("ar", ar, AR_LAMA)):
    n = teks.count(lama)
    if n != 1:
        print(f"GAGAL: jangkar {nama} cocok {n}x — NOL ditulis"); sys.exit(1)

kepala = """-- V245 — compute_ap_outstanding / compute_ar_outstanding: pembayaran multi-alokasi PROPORSIONAL.
--
-- Cacat (terukur lewat eksekusi, handler nyata, ROLLBACK, 13 Sep 2026): pembayaran 2.000 dengan
-- dua alokasi 1.000 menurunkan outstanding MASING-MASING dokumen 2.000. GL BENAR di kedua sisi —
-- buku besar pemilik tidak salah; yang salah laporan per dokumen. Cache AR (dihitung ulang dari
-- fungsi) ikut ganda -> faktur bisa tampil "lunas" padahal belum. Kedua form FE membangun
-- multi-alokasi. Data hari ini: 0 pembayaran multi-alokasi -> nol dokumen historis berubah.
--
-- Hanya dua CTE diganti (dibangkitkan dari definisi hidup, jangkar di-assert). ROLLBACK =
-- definisi hidup sebelum V245, apa adanya.
"""

ap_baru = ap.replace(AP_LAMA, AP_BARU)
ar_baru = ar.replace(AR_LAMA, AR_BARU)
open(M + "V245__alokasi_proporsional_ap_ar.sql", "w").write(
    kepala + "\nBEGIN;\n\n" + ap_baru + ";\n\n" + ar_baru + ";\n\nCOMMIT;\n")
open(M + "V245__alokasi_proporsional_ap_ar_ROLLBACK.sql", "w").write(
    "-- ROLLBACK V245 — definisi HIDUP sebelum V245, disalin apa adanya dari pg_get_functiondef.\n"
    "-- Sesudah ini multi-alokasi kembali dihitung ganda per dokumen. NOL data disentuh.\n\nBEGIN;\n\n"
    + ap + ";\n\n" + ar + ";\n\nCOMMIT;\n")
print("V245 + ROLLBACK dibangkitkan")
