# LAPORAN — refactor boilerplate router (get_pool / get_user_context)

Cabang: `refactor/router-boilerplate` (dari `master` @ 036b1f1). Sesi: CLOUD 5. Tanggal: 2026-11-04.
Kanal laporan ini = satu-satunya jalur balik ke MASTER. Diperbarui tiap tahap.

## Status tahap

| Tahap | Status |
|---|---|
| 1. Ukur & kelompokkan varian | SELESAI (tabel di bawah) |
| 2. Ganti salinan identik ke satu sumber | SELESAI (99 router disentuh, 1 modul baru) |
| 3. Hapus salinan di commit yang sama | SELESAI (commit tunggal) |
| 4. Gerbang py_compile + pyflakes | SELESAI, lolos |
| 5. Gerbang `import app.main` + pytest unit | SELESAI, lolos (lihat Gerbang) |
| 6. Push cabang | LIHAT bagian Akses push |

## Ringkasan angka

| Ukuran | Nilai |
|---|---|
| Router disentuh | 99 (dari 149 berkas di `routers/`) |
| Definisi `get_pool` dihapus | 98 berkas, 464 baris definisi |
| Definisi `get_user_context` dihapus | 50 berkas, 530 baris definisi |
| Definisi `get_user_context` DIBIARKAN (varian beda perilaku) | 32 berkas, 346 baris |
| Impor mubazir ikut dibersihkan (hanya yang jadi mubazir karena refactor ini) | 73 baris: `import asyncpg` ×66, `get_db_pool` tanpa alias ×1, `HTTPException` ×2, `UUID` ×2 |
| Diff router | 99 berkas, +150 / −1360, bersih −1210 baris |
| Modul baru `dependencies/user_context.py` | +55 baris |
| Bersih total | ≈ −1155 baris |

Catatan hitungan: angka MASTER (±567 / ±946) termasuk baris kosong & docstring pemisah; di sini dihitung baris definisi fungsi saja (AST `lineno..end_lineno`).

## Tahap 1 — kelompok varian (normalisasi AST, docstring diabaikan)

### `get_pool` — 98 definisi, 4 bentuk teks, SEMUA identik secara perilaku (`return await get_db_pool()`)

| Kelompok | Berkas | Baris | Bentuk | Tindakan |
|---|---|---|---|---|
| P1 | 78 | 388 | `async def get_pool() -> asyncpg.Pool:` + docstring Law 32 + impor lokal `..services.db_pool` | diganti alias impor |
| P2 | 18 | 70 | sama tanpa anotasi balik | diganti alias impor |
| P3 | 1 (`units.py`) | 2 | memakai impor modul-level `get_db_pool` | diganti alias impor; impor lama yang jadi mubazir dihapus |
| P4 | 1 (`search.py`) | 4 | impor absolut `backend.api_gateway.app.services.db_pool` | diganti alias impor dengan jalur absolut yang SAMA (gaya berkas itu dipertahankan) |

Pengganti: `from ..services.db_pool import get_db_pool as get_pool  # singleton pool (Law 32)`.
Nama modul `get_pool` tetap ada, jadi (a) `monkeypatch.setattr(mod, "get_pool", ...)` di tests/unit tetap bekerja, (b) `from .purchase_orders import get_pool` (goods_receipts) dan `from .customers import get_pool` (alamat_pelanggan) tetap bekerja, (c) `lampiran_induk.dep("get_pool")` yang membaca `getattr(sys.modules[modul_dep], "get_pool")` tetap bekerja — dua berkas (`delivery_lampiran.py`, `lampiran_induk.py`) diberi `# noqa: F401` karena pyflakes tak melihat pemakaian dinamis itu.

Perbedaan perilaku yang disadari: impor `db_pool` kini EAGER (saat modul router dimuat) bukan lazy di dalam fungsi. `services/db_pool.py` hanya mengimpor `asyncpg`, `os`, `logging`, `..config` (config: os/dotenv/typing), tak ada jalur balik ke `routers`, jadi tak ada siklus impor. 12 router sudah mengimpor `db_pool` di modul-level sebelum ini, dan `main.py` memuat `db_pool` saat startup; tidak ada perubahan urutan inisialisasi pool.

### `get_user_context` — 82 definisi, 19 bentuk teks

Kunci pembeda: (1) teks pesan 401, (2) tipe `user_id` (UUID vs str apa adanya), (3) kunci dict yang dikembalikan, (4) ada/tidak pemeriksaan tenant, (5) fallback `user.get("id")`.

| Kelompok | Berkas | Baris | Ciri | Tindakan |
|---|---|---|---|---|
| A | 29 | 357 | pesan "Silakan masuk…"/"Sesi tidak valid…", `user_id: UUID(user_id) if user_id else None` | → `user_context.get_user_context` |
| H | 2 (`price_lists`, `storage_locations`) | 22 | = A, ditulis `UUID(user.get("user_id")) if user.get("user_id")` | → `get_user_context` |
| N | 1 (`uploads`) | 11 | = A, `request` tanpa anotasi, `_UUID`, impor HTTPException lokal; dipanggil langsung `get_user_context(request)` (bukan `Depends`) | → `get_user_context` |
| C | 11 | 88 | pesan sama, `user_id` apa adanya (str) | → `get_user_context_str as get_user_context` |
| D | 5 (`efaktur`, `product_djp_mapping`, `djp_master`, `nsfp`, `tax_invoices`) | 35 | = C, `user.get("tenant_id")` dibaca dua kali | → `get_user_context_str` |
| K | 1 (`employee_advances`) | 8 | = C, `getattr(request.state,"user",None)` (ekuivalen hasattr+falsy) | → `get_user_context_str` |
| R | 1 (`kasbank`) | 9 | = C, variabel sementara | → `get_user_context_str` |
| **B** | **13** | 91 | TANPA cek tenant, TANPA cek falsy user; `request.state.user["tenant_id"]` → KeyError/500 bila tenant kosong | **DIBIARKAN** (perilaku beda: 500 vs 401) |
| **E** | 3 (`psak_reports`, `ledger`, `suppliers`) | 28 | pesan A, hanya kunci `tenant_id` (tanpa `user_id`) | DIBIARKAN (kunci dict beda) |
| **F** | 3 (`quotes`, `sales_orders`, `proformas`) | 48 | pesan "Perlu masuk (login)."/"Konteks pengguna tidak sah.", fallback `user.get("id")`, `uuid_module.UUID` | DIBIARKAN (pesan + fallback beda) |
| **G** | 3 (`customer_deposits`, `credit_notes`, `receive_payments`) | 39 | pesan "Perlu masuk…", UUID | DIBIARKAN (pesan beda) |
| **I** | 2 (`deliveries`, `sales_invoice_link_order`) | 16 | pesan "Perlu masuk…", str | DIBIARKAN (pesan beda) |
| **J** | 2 (`approvals`, `audit`) | 36 | + `user_email`, `user_role` | DIBIARKAN (kunci tambahan) |
| **L+O** | 2 (`currencies`, `bank_reconciliation`) | 25 | pesan A, UUID, fallback `user.get("id")` — keduanya identik satu sama lain | DIBIARKAN (fallback beda dari A; hanya 2 salinan, belum layak jadi varian ketiga) |
| **M** | 1 (`payroll`) | 18 | + `username`, `business_role_code` | DIBIARKAN |
| **P** | 1 (`sales_invoices`) | 17 | pesan "Perlu masuk…", UUID dibungkus try/except → None bila tak valid | DIBIARKAN |
| **Q** | 1 (`reports`) | 13 | `user_id` str + kunci `user` (dict utuh) | DIBIARKAN |
| **S** | 1 (`items`) | 15 | + `username`, `request` tanpa anotasi | DIBIARKAN |

Total diganti: A+H+N = 32 berkas → `get_user_context` (UUID); C+D+K+R = 18 berkas → `get_user_context_str`. Total dibiarkan: 32 berkas / 346 baris.

## Tahap 2 — sumber tunggal

- `get_pool` → `services/db_pool.get_db_pool` (sudah ada; Law 32).
- `get_user_context` → modul BARU `backend/api_gateway/app/dependencies/user_context.py` berisi `get_user_context` (user_id UUID) dan `get_user_context_str` (user_id apa adanya), teks 401 dan kunci dict persis sama dengan salinan lama; urutan pemeriksaan sama (user → tenant).
- Kenapa tidak ditaruh di `dependencies/auth.py`: modul itu mengimpor `backend.api_gateway.app.services.auth_instance` (jalur ABSOLUT) yang membuat `AuthClient` gRPC saat impor. Tes unit memuat router sebagai paket `app.*` (`sys.path=/app/backend/api_gateway`), sehingga 50 router yang ikut mengimpor jalur absolut itu berisiko impor ganda (`app.services.X` vs `backend.api_gateway.app.services.X`, dua singleton) dan menarik gRPC ke tiap tes router. Modul `user_context.py` hanya mengimpor `fastapi` + `uuid`. `dependencies/auth.py` tidak diubah.
- Perlu diketahui: ada `dependencies/permissions.get_user_context` (async, mengembalikan `UserContext` dari PolicyEngine) — fungsi berbeda, tidak diimpor router mana pun, tidak disentuh.

## Berkas disentuh (99 router + 1 modul baru + laporan ini)

| Berkas | Fungsi dihapus | Diff |
|---|---|---|
| `accounting_settings.py` | get_pool | +1 / -7 |
| `accounts.py` | get_user_context, get_pool | +2 / -23 |
| `approvals.py` | get_pool | +1 / -8 |
| `audit.py` | get_pool | +1 / -8 |
| `auth.py` | get_pool | +1 / -8 |
| `bank_accounts.py` | get_user_context, get_pool | +2 / -23 |
| `bank_reconciliation.py` | get_pool | +1 / -8 |
| `bank_transfers.py` | get_user_context, get_pool | +2 / -23 |
| `bill_payments.py` | get_user_context, get_pool | +2 / -19 |
| `bills.py` | get_user_context, get_pool | +2 / -22 |
| `bom.py` | get_user_context, get_pool | +2 / -22 |
| `branches.py` | get_user_context, get_pool | +2 / -23 |
| `budgets.py` | get_pool | +1 / -8 |
| `bulk.py` | get_pool | +1 / -5 |
| `bulk_dokumen.py` | get_pool | +1 / -5 |
| `bulk_hapus_beli.py` | get_pool | +1 / -5 |
| `bulk_po.py` | get_pool | +1 / -5 |
| `bulk_proforma.py` | get_pool | +1 / -5 |
| `bulk_quote.py` | get_pool | +1 / -5 |
| `bulk_so.py` | get_pool | +1 / -5 |
| `cheques.py` | get_user_context, get_pool | +2 / -22 |
| `consolidation.py` | get_user_context, get_pool | +2 / -22 |
| `cost_centers.py` | get_pool | +1 / -8 |
| `credit_notes.py` | get_pool | +1 / -8 |
| `currencies.py` | get_pool | +1 / -8 |
| `customer_deposits.py` | get_pool | +1 / -7 |
| `customers.py` | get_user_context, get_pool | +2 / -23 |
| `deliveries.py` | get_pool | +1 / -6 |
| `delivery_lampiran.py` | get_pool | +1 / -7 |
| `djp_master.py` | get_user_context, get_pool | +3 / -18 |
| `document_intake.py` | get_user_context, get_pool | +2 / -23 |
| `documents.py` | get_pool | +1 / -8 |
| `efaktur.py` | get_user_context, get_pool | +2 / -17 |
| `employee_advances.py` | get_user_context, get_pool | +2 / -17 |
| `employees.py` | get_user_context, get_pool | +2 / -18 |
| `expense_extended.py` | get_user_context, get_pool | +2 / -18 |
| `expenses.py` | get_user_context, get_pool | +2 / -23 |
| `financial_ratios.py` | get_user_context, get_pool | +2 / -24 |
| `fiscal_years.py` | get_user_context, get_pool | +2 / -22 |
| `fixed_assets.py` | get_pool | +1 / -8 |
| `intercompany.py` | get_user_context, get_pool | +2 / -23 |
| `item_aliases.py` | get_user_context, get_pool | +2 / -17 |
| `item_batches.py` | get_pool | +1 / -8 |
| `item_serials.py` | get_pool | +1 / -8 |
| `items.py` | get_pool | +1 / -7 |
| `journals.py` | get_user_context, get_pool | +2 / -23 |
| `kasbank.py` | get_user_context, get_pool | +2 / -19 |
| `kasbank_v2.py` | get_user_context, get_pool | +2 / -23 |
| `kds.py` | get_user_context, get_pool | +2 / -19 |
| `lampiran_induk.py` | get_pool | +1 / -5 |
| `ledger.py` | get_pool | +1 / -8 |
| `matrix_items.py` | get_pool | +1 / -7 |
| `members.py` | get_pool | +1 / -8 |
| `metode_serah.py` | get_pool | +1 / -6 |
| `nsfp.py` | get_user_context, get_pool | +2 / -17 |
| `opening_balance.py` | get_user_context, get_pool | +2 / -23 |
| `payroll.py` | get_pool | +1 / -8 |
| `payroll_config.py` | get_user_context, get_pool | +3 / -19 |
| `payroll_payments.py` | get_user_context, get_pool | +2 / -18 |
| `payroll_runs.py` | get_user_context, get_pool | +2 / -18 |
| `periods.py` | get_user_context, get_pool | +2 / -23 |
| `pkp_settings.py` | get_user_context, get_pool | +2 / -18 |
| `pkp_status.py` | get_pool | +1 / -7 |
| `price_lists.py` | get_user_context, get_pool | +2 / -20 |
| `product_djp_mapping.py` | get_user_context, get_pool | +2 / -17 |
| `production.py` | get_user_context, get_pool | +2 / -23 |
| `production_costing.py` | get_user_context, get_pool | +2 / -19 |
| `proformas.py` | get_pool | +1 / -8 |
| `purchase_orders.py` | get_user_context, get_pool | +2 / -22 |
| `quotes.py` | get_pool | +1 / -8 |
| `receive_payments.py` | get_pool | +1 / -8 |
| `recipes.py` | get_user_context, get_pool | +2 / -19 |
| `recurring_bills.py` | get_pool | +1 / -8 |
| `reports.py` | get_pool | +1 / -8 |
| `salary_components.py` | get_user_context, get_pool | +2 / -17 |
| `sales_invoice_link_order.py` | get_pool | +1 / -7 |
| `sales_invoices.py` | get_pool | +1 / -7 |
| `sales_order_lampiran.py` | get_pool | +1 / -7 |
| `sales_orders.py` | get_pool | +1 / -8 |
| `sales_receipts.py` | get_pool | +1 / -8 |
| `search.py` | get_pool | +1 / -7 |
| `so_fase_lokasi.py` | get_pool | +1 / -5 |
| `stock_adjustments.py` | get_user_context, get_pool | +2 / -23 |
| `stock_transfers.py` | get_pool | +1 / -8 |
| `storage_locations.py` | get_user_context, get_pool | +2 / -21 |
| `suppliers.py` | get_pool | +1 / -8 |
| `tables.py` | get_user_context, get_pool | +2 / -19 |
| `tax_codes.py` | get_user_context, get_pool | +2 / -22 |
| `tax_groups.py` | get_user_context, get_pool | +2 / -17 |
| `tax_invoices.py` | get_user_context, get_pool | +2 / -17 |
| `tax_reports.py` | get_pool, get_user_context | +2 / -18 |
| `team_members.py` | get_pool | +1 / -8 |
| `units.py` | get_user_context, get_pool | +2 / -15 |
| `uploads.py` | get_user_context | +1 / -13 |
| `vendor_credits.py` | get_user_context, get_pool | +2 / -23 |
| `vendor_deposits.py` | get_pool | +1 / -8 |
| `vendors.py` | get_user_context, get_pool | +2 / -23 |
| `warehouse_bins.py` | get_pool | +1 / -7 |
| `warehouses.py` | get_pool | +1 / -8 |
| `backend/api_gateway/app/dependencies/user_context.py` | (baru) | +55 |

Berkas `routers/` yang TIDAK disentuh sama sekali: 50 (tidak mendefinisikan `get_pool`/`get_user_context`, atau varian dibiarkan: `items.py` hanya ctx-nya dibiarkan tapi `get_pool`-nya diganti).

## Yang sengaja tidak diubah + alasan

1. 32 varian `get_user_context` di tabel (B, E, F, G, I, J, L+O, M, P, Q, S): perilaku berbeda (pesan 401 / tipe user_id / kunci ctx / tanpa cek tenant). Mengubahnya = perubahan kontrak HTTP, butuh putusan pemilik.
2. Logika bisnis, jurnal, uang: nol baris disentuh. Semua hunk hanya impor + blok `def get_pool` / `def get_user_context`.
3. `search.py` tetap gaya impor absolut (konsisten dengan dua impor lain di berkas itu).
4. Impor yang sudah mubazir SEBELUM refactor ini (pyflakes di versi `master`) tidak disentuh — hanya yang BARU mubazir akibat penghapusan fungsi.

## Rekomendasi tindak lanjut (bukan bagian commit ini)

- Kelompok B (13 router: `budgets`, `cost_centers`, `documents`, `fixed_assets`, `item_batches`, `item_serials`, `matrix_items`, `recurring_bills`, `sales_receipts`, `stock_transfers`, `vendor_deposits`, `warehouse_bins`, `warehouses`): tanpa cek tenant → 500 alih-alih 401 bila sesi tanpa tenant. Kalau pemilik setuju menyamakan ke 401, semuanya bisa diarahkan ke `get_user_context_str` (1 baris/berkas, −91 baris).
- Keluarga pesan "Perlu masuk (login)." (F, G, I, P: 9 router dokumen penjualan): bisa dijadikan varian parametris bila teks pesan 401 disepakati satu.

## Gerbang (dijalankan di cloud, Python 3.13.18, venv dari requirements.txt minus grpcio-tools)

Semua gerbang dijalankan DUA kali: pada pohon dasar `master` @ 036b1f1 (worktree) dan pohon refactor, agar galat lingkungan terpisah dari regresi.

| Gerbang | master 036b1f1 | refactor 851f531 | Catatan |
|---|---|---|---|
| `python -m py_compile` 100 berkas tersentuh | — | LOLOS (0 gagal) | |
| pyflakes per berkas, sebelum vs sesudah | (acuan) | 0 pesan baru | 2 F401 sengaja (`delivery_lampiran`, `lampiran_induk`: pemakaian dinamis via `getattr(sys.modules[...])`), diberi noqa |
| impor langsung 99 router tersentuh + `user_context` | 97/100 | 97/100 | 3 yang gagal sama persis di kedua pohon (`auth`, `search`: impor absolut `backend.api_gateway.app`; `reports`: `accounting_kernel`) = PYTHONPATH kontainer belum disetel, bukan regresi; lolos setelah PYTHONPATH setara Dockerfile |
| `python -c "import app.main"` | LOLOS, 1400 rute | LOLOS, 1400 rute | butuh PYTHONPATH setara Dockerfile (`$R:$R/backend/api_gateway:$R/backend/api_gateway/libs:$R/backend/services`) dan `OPENAI_API_KEY` dummy (dicek saat impor); keduanya galat lingkungan |
| pytest `tests/unit` — 323 berkas tes yang mengimpor router tersentuh (`-o addopts=""`, tanpa DB) | 5068 passed, 3 skipped, rc 0 (81 s) | 5068 passed, 3 skipped, rc 0 (80 s) | daftar gagal dasar ∖ cabang = kosong; cabang ∖ dasar = kosong |

Yang TIDAK bisa dijalankan di cloud: `pip install -r requirements.txt` utuh (grpcio-tools==1.63.0 tak punya wheel Python 3.13; paket lain terpasang per-paket), tes yang butuh DB/gRPC hidup, dan smoke HTTP 401 di server. Server harap menjalankan gerbang resmi.

## Risiko

| Risiko | Tingkat | Mitigasi |
|---|---|---|
| Alias impor eager → siklus impor | rendah | diperiksa: `db_pool`→`config` saja; `user_context`→fastapi/uuid saja |
| Tes yang menambal `mod.get_pool` / `mod.get_user_context` | rendah | atribut modul tetap ada (alias), setattr tetap bekerja |
| `lampiran_induk.dep("get_pool")` membaca atribut modul pemilik | rendah | alias dipertahankan + noqa; diverifikasi `getattr(sys.modules[...])` |
| `Depends(get_user_context)` berubah signature | nihil | signature canonical `(request: Request) -> dict` sama dengan 49 dari 50 salinan; `uploads.py` memanggil langsung, bukan Depends |
| Salinan "identik" ternyata beda | rendah | pengelompokan via AST unparse (docstring/komentar/spasi diabaikan), lalu dibaca manual per kelompok |

## Akses push

Repo tertaut ke sesi (owner meminta ulang izin), tetapi pemeriksaan push menyatakan Claude GitHub App belum terpasang/terhubung untuk `messi10thegoat/milkyhoop-backend` → push akan ditolak. Langkah pemilik: hubungkan ulang GitHub di https://claude.ai/connect-github dan pastikan Claude GitHub App terpasang di repo ini. Commit sudah ada di clone lokal sesi; push dicoba ulang setelah akses ada.
