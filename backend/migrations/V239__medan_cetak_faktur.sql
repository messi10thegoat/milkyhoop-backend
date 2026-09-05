-- V239: medan cetak yang selama ini tidak punya kolomnya.
--
-- Ketiganya lahir dari perbandingan cetakan template B dengan faktur acuan
-- pemilik: barisnya ADA di acuan tapi datanya tak punya tempat di sini, jadi
-- pemilik menyiasatinya dengan mengetik semuanya ke satu medan alamat --
-- hasilnya header tercetak "Head Office : Head Office : ..." dan teks
-- Workshop ikut masuk ke blok Head Office.
--
-- Ketiganya NULL-able dan tanpa nilai bawaan: barisnya hanya dicetak bila
-- terisi. Baris kosong berlabel ("Workshop :" tanpa isi) lebih buruk daripada
-- tidak ada baris -- ia terlihat seperti data yang hilang, bukan medan yang
-- belum dipakai.

ALTER TABLE "Tenant"
    ADD COLUMN IF NOT EXISTS workshop_address text,
    ADD COLUMN IF NOT EXISTS signatory_name   text;

ALTER TABLE bank_accounts
    ADD COLUMN IF NOT EXISTS bank_address text;

COMMENT ON COLUMN "Tenant".workshop_address IS
    'Alamat bengkel/workshop untuk kop faktur. Kosong = baris Workshop tidak dicetak.';
COMMENT ON COLUMN "Tenant".signatory_name IS
    'Nama penandatangan di bawah "Faithfully yours,". Kosong = memakai nama tenant.';
COMMENT ON COLUMN bank_accounts.bank_address IS
    'Alamat kantor cabang bank untuk blok pembayaran di faktur. Cabangnya '
    'sendiri sudah ada di bank_branch.';
