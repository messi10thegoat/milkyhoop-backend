# Tipe akun bank: PATCH account_type + perbaikan split kartu — LIVE

**Commit:** `fb771141` (deploy/master) · **Tanggal:** 2026-09-14 · **Gateway:** healthz 200

## Masalah (terukur)
grapgrap-manado punya 3 akun (dibuat 2026-09-14 oleh Admin/Erni): 2 bernama "BCA…" tapi
`bank_accounts.account_type` = `cash`/`petty_cash` (semestinya `bank`). **Bukan cacat form** —
kemungkinan tipe kas terpilih saat buat. CoA tertaut (1-10201/2/3) sudah BENAR: ASSET, is_cash,
ada jurnal → immutable (Law 18), TIDAK disentuh. Yang keliru hanya LABEL.

**Akar:** `PATCH /api/bank-accounts/{id}` tak punya `account_type` (schema & handler) → pemilik tak
bisa perbaiki via Edit. Label hanya bisa diset saat CREATE.

**Dampak label (terukur):** buku besar/neraca/laba-rugi/arus kas TAK terpengaruh (pakai CoA
account_type + is_cash). Satu-satunya efek: kartu Kas vs Bank di ringkasan Kas&Bank.

## Perbaikan
1. **PATCH account_type** (`UpdateBankAccountRequest` + `update_bank_account`): validasi konsistensi
   CoA (non-credit_card→ASSET, credit_card→LIABILITY); **larang transisi ke/dari credit_card → 400**
   (butuh ganti tipe CoA yang Law 18 blokir bila ada jurnal); UPDATE murni `bank_accounts`, tanpa
   jurnal/CoA (label-only, nol perubahan akuntansi).
2. **Split kartu Kas&Bank** (bug ke-2, 2 situs — `/summary` python & `/stats` SQL): dulu hanya
   `cash`/`bank` dihitung → saldo `petty_cash` & `e_wallet` **HILANG** dari ringkasan. Kini:
   `cash+petty_cash → Kas`, `bank+e_wallet → Bank`. `credit_card` tetap dikecualikan (liabilitas).

## Gerbang dua-sisi (kontainer, savepoint ROLLBACK, handler+SQL NYATA) — 9/9 GREEN
- A: cash→bank & petty_cash→bank **200 & tersimpan**; ke/dari credit_card **400**; CoA tak-konsisten
  **400**; **handler LAMA cash→bank → account_type TETAP 'cash'** (RED-alat).
- B: ekspresi CASE diekstrak dari sumber (baru: file patched; lama: `git show`), dijalankan atas
  dataset VALUES. **Σ(kartu)=Σ(aktif non-cc)=650, tak ada hilang**; kode LAMA **Σ=600, kehilangan 50**
  (petty_cash+e_wallet) = RED; credit_card (−200) tak masuk kartu mana pun.

## Untuk pemilik
Pemilik kini bisa buka **Barang→Kas&Bank→Edit** tiap akun BCA, ubah tipe `Kas`→`Bank`, simpan. FE r61
sudah mengirim `account_type` di PATCH. Data pemilik TIDAK aku ubah.
