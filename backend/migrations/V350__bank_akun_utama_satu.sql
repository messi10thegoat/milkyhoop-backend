-- V350 (MASTER 30 Sep 2026, "default bisa-ditimpa" tahap 1): SATU rekening utama (is_default) per tenant, dijaga DB.
-- Dulu hanya konvensi kode bank_accounts.py (UPDATE is_default=false sebelum set). Invariant yang bisa dinyatakan
-- sebagai predikat baris ditegakkan di DB (ironlaws). Diukur 30 Sep: tak ada tenant dengan >1 is_default=true
-- (termasuk akun nonaktif) -> aman. Penentu default_dokumen.default_pesanan membaca bendera ini.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_accounts_satu_utama
    ON bank_accounts (tenant_id) WHERE is_default;
