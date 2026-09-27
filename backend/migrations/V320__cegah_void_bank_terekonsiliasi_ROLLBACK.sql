-- Rollback V320: lepas penjaga void bank terekonsiliasi (indeks dibiarkan: aditif, tak berbahaya).
DROP TRIGGER IF EXISTS trg_cegah_void_bank_terekonsiliasi ON journal_entries;
DROP FUNCTION IF EXISTS cegah_void_bank_terekonsiliasi();
