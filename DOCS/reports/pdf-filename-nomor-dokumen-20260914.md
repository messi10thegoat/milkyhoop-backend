# Nama file PDF = nomor dokumen telanjang + filename* UTF-8 — LIVE

**Commit:** `903990e1` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Perubahan
Helper bersama `app/utils/content_disposition.py` → `pdf_content_disposition(base)`:
- sanitasi karakter tak sah (`< > : " / \ | ? *` + kontrol) → `-` (nomor ber-`/` aman),
- `filename="<ascii>.pdf"` (fallback ASCII) **+** `filename*=UTF-8''<pct>` (RFC 5987; nama non-ASCII utuh),
- kosong → `dokumen.pdf`.

Semua PDF dokumen kini memakai **nomor dokumen PERSIS** (tanpa awalan; nomor sudah membawa awalan
INV-/QUO-/DEP-/dst).

## Nama file akhir per endpoint (untuk FE — pakai aturan sama di penampil PDF)
| Endpoint | Nama file |
|---|---|
| `GET /api/sales-invoices/{id}/pdf` | `{invoice_number}.pdf` |
| `GET /api/bills/{id}/pdf` | `{invoice_number}.pdf` (bills hanya punya `invoice_number`) |
| `GET /api/quotes/{id}/pdf` | `{quote_number}.pdf` |
| `GET /api/proformas/{id}/pdf` | `{proforma_number}.pdf` |
| `GET /api/deliveries/{id}/pdf` | `{delivery_number}.pdf` |
| `GET /api/receive-payments/{id}/pdf` | `{receipt_number}.pdf` |
| `GET /api/customer-deposits/{id}/pdf` | `{deposit_number}.pdf` |
| Laporan (psak_reports) | `Laba-Rugi_{display_name}_{period_start}-{as_of}.pdf` · `Posisi-Keuangan_{display_name}_{as_of}.pdf` · `Arus-Kas_{display_name}_{period_start}-{as_of}.pdf` |

Semua disanitasi (illegal→`-`) + `filename*` UTF-8. Fallback `id[:8]` bila nomor null.
`bills` JSON presigned-URL juga mengembalikan `filename` telanjang (bukan berawalan).

## Gerbang dua-sisi (host, helper NYATA + diff sumber baru-vs-lama `git show`) — 14/14 GREEN
Helper: filename==nomor; `/`→`-` (kedua sisi); non-ASCII utuh di `filename*` (Ω=`%CE%A9`); `filename*`
selalu ada; kosong→`dokumen.pdf`. Tiap dari 8 endpoint: baru pakai `pdf_content_disposition(<nomor>)`
tanpa awalan; lama **berawalan** (RED). Smoke runtime in-container: `INV/2026/1`→`INV-2026-1.pdf`,
`Toko Ω`→fallback `Toko -.pdf` + `filename*` `Toko%20%CE%A9.pdf`.
