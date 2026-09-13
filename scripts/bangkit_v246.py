"""Bangkitkan V246 dari definisi HIDUP hc_ap_members (V242).

Pemeriksa check_7 punya cacat hitung-ganda yang SAMA dgn compute_ap_outstanding (gl_pay per tagihan =
net SELURUH jurnal pembayaran). V246 menulis ulang gl_pay PROPORSIONAL dengan EKSPRESI SENDIRI:
subkueri berkorelasi + net jurnal efektif (credit-debit, is_effective_journal) milik pemeriksa --
TIDAK memanggil CTE/fungsi jendela/helper milik compute_ap_outstanding. Dua implementasi independen
dari satu spesifikasi (ROUND 2 desimal, sisa sen ke alokasi id terbesar) yang wajib sepakat.
"""
import subprocess
import sys

M = "/root/mh-law2/backend/migrations/"
r = subprocess.run(["docker", "exec", "milkyhoop-dev-postgres-1", "psql", "-U", "postgres", "-d", "milkydb", "-At",
                    "-c", "SELECT pg_get_functiondef('hc_ap_members(text)'::regprocedure)"],
                   capture_output=True, text=True, check=True)
hidup = r.stdout.rstrip("\n")

LAMA = """          + COALESCE((SELECT SUM(g.net) FROM gl g
                      JOIN bill_payments_v2 p ON p.journal_id = g.id
                      JOIN bill_payment_allocations a ON a.payment_id = p.id
                      WHERE a.bill_id = b.id), 0) AS gl_net"""

BARU = """          -- V246: PROPORSIONAL, ekspresi SENDIRI (independen dari fungsi yang diperiksa).
          -- bagian alokasi = ROUND(net jurnal pembayaran * applied / Σapplied pembayaran, 2);
          -- alokasi id terbesar menyerap sisa sen. Versi V242 menjumlahkan net SELURUH jurnal
          -- pembayaran ke setiap tagihan yang dialokasi -> hitung-ganda pada multi-alokasi.
          + COALESCE((SELECT SUM(
                    CASE WHEN (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id) > 0
                         THEN ROUND(g.net * a.amount_applied
                                    / (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id), 2)
                         ELSE 0 END
                  + CASE WHEN a.id = (SELECT a3.id FROM bill_payment_allocations a3 WHERE a3.payment_id = p.id ORDER BY a3.id DESC LIMIT 1)
                          AND (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id) > 0
                         THEN g.net - (SELECT SUM(ROUND(g.net * a4.amount_applied
                                                        / (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id), 2))
                                       FROM bill_payment_allocations a4 WHERE a4.payment_id = p.id)
                         ELSE 0 END)
                FROM gl g
                JOIN bill_payments_v2 p ON p.journal_id = g.id
                JOIN bill_payment_allocations a ON a.payment_id = p.id
                WHERE a.bill_id = b.id), 0) AS gl_net"""

if hidup.count(LAMA) != 1:
    print("GAGAL: jangkar gl_pay cocok %dx — NOL ditulis" % hidup.count(LAMA)); sys.exit(1)
# Independensi = rumus gl_pay tak memakai konstruksi milik fungsi yang diperiksa (nama CTE V245,
# fungsi jendela, pemanggilan fungsinya). Komentar dibuang dulu supaya teks penjelasan tak memicu
# positif palsu (run pertama memerah karena kata di komentar).
import re as _re
_kode = "\n".join(_re.sub(r"--.*$", "", ln) for ln in BARU.split("\n"))
if "compute_ap_outstanding" in _kode or "pd_bayar" in _kode or "pd_bagian" in _kode or "OVER (" in _kode:
    print("GAGAL: ekspresi baru tidak independen"); sys.exit(1)

open(M + "V246__hc_ap_members_proporsional.sql", "w").write(
    "-- V246 — pemeriksa check_7 (hc_ap_members) PROPORSIONAL, ditulis INDEPENDEN dari compute_ap_outstanding.\n"
    "-- Harus di-deploy BERSAMA V245: tanpa ini check_7 memerah palsu pada pembayaran multi pertama.\n"
    "-- Gerbang: scripts/gerbang_v245.py (gabungan V245+V246, sabotase dua arah).\n\nBEGIN;\n\n"
    + hidup.replace(LAMA, BARU) + ";\n\nCOMMIT;\n")
open(M + "V246__hc_ap_members_proporsional_ROLLBACK.sql", "w").write(
    "-- ROLLBACK V246 — hc_ap_members persis definisi hidup sebelum V246 (V242).\n\nBEGIN;\n\n" + hidup + ";\n\nCOMMIT;\n")
print("V246 + ROLLBACK dibangkitkan")
