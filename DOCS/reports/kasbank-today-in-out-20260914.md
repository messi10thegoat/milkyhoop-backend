# kasbank today_in/out: himpunan benar — LIVE

**Commit:** `495d7589` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Bug (terukur)
`GET /api/kasbank/stats` `today_in`/`today_out` menyaring `coa.account_type IN ('BANK','CASH')`.
Tapi `chart_of_accounts.account_type` **tak pernah** bernilai `BANK`/`CASH` (CHECK: ASSET/LIABILITY/…;
CoA bank = `ASSET` + `is_cash`). → filter cocok **NOL baris** → **today_in & today_out SELALU 0**.
Live grapgrap (gerakan bank hari ini) seharusnya in=6.152.090, out=0 — endpoint balas 0/0.

## Perbaikan — himpunan dinyatakan
- **akun**: tautan `bank_accounts` aktif (`jl.account_id IN (SELECT coa_id FROM bank_accounts WHERE
  tenant_id=$1 AND is_active)`), konsisten dg `cash_total`/`bank_total`.
- **EFEKTIF**: `reversed_by_id IS NULL AND reversal_of_id IS NULL` — void hari ini tak menambah masuk+keluar.
- **tanggal**: hari ini di **zona waktu tenant** (`now() AT TIME ZONE Tenant.timezone`, grapgrap =
  `Asia/Jakarta`), bukan `CURRENT_DATE` server.
- **KECUALIKAN `BANK_TRANSFER`**: pindah antar-rekening sendiri = internal, bukan arus.

## Gerbang dua-sisi (data NYATA grapgrap, SQL diekstrak dari sumber baru-vs-lama) — 6/6 GREEN
| | nilai |
|---|---|
| BARU in / out | **6.152.090 / 0** (== TRUTH journal-derived via JOIN independen) |
| LAMA in / out | **0 / 0** (RED — filter tak cocok) |
| exclude-transfer (in) | noExcl 8.370.490 − transfer 2.218.400 = 6.152.090 ✓ |
| exclude-transfer (out) | noExcl 2.218.400 − 2.218.400 = 0 ✓ |
| kontrol efektif | filter `reversal_of_id` tak buang data nyata hari ini (tak ada reversal) ✓ |

## Caveat (dilaporkan)
Transfer via **MANUAL journal** antar dua bank (tanpa `source_type='BANK_TRANSFER'`) tak terdeteksi
sebagai internal → akan tampil sebagai arus. `BANK_TRANSFER` adalah penanda transfer yang dirancang;
transfer lewat jurnal manual tak bisa dibedakan dari arus nyata tanpa analisis dua-kaki. Owner-gate bila
perlu dibedakan.
