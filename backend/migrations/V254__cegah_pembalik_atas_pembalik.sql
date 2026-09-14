-- V254 (14 Sep 2026) — Law 26 di DB: cegah pembalikan ATAS pembalikan. Skill ironlaws mengklaim trigger
-- trg_prevent_reverse_of_reversal tapi TAK ADA (terukur 14 Sep: 6 trigger di journal_entries, ia bukan salah satunya).
-- Predikat: INSERT jurnal ber-reversal_of_id yang menunjuk jurnal yang ITU SENDIRI ber-reversal_of_id -> tolak.
-- Historis pembalik-atas-pembalik = 0 (terukur seluruh tenant), jadi trigger BEFORE INSERT tak menabrak data lama.

DO $$
DECLARE v_n bigint;
BEGIN
    SELECT count(*) INTO v_n FROM journal_entries r JOIN journal_entries o ON o.id = r.reversal_of_id
    WHERE o.reversal_of_id IS NOT NULL;
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'V254: ada % pembalik-atas-pembalik historis (diharapkan 0) — BERHENTI, jangan pasang tanpa lapor', v_n;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION cegah_pembalik_atas_pembalik()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_induk_pembalik uuid;
BEGIN
    IF NEW.reversal_of_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT reversal_of_id INTO v_induk_pembalik FROM journal_entries WHERE id = NEW.reversal_of_id;
    IF v_induk_pembalik IS NOT NULL THEN
        RAISE EXCEPTION 'Law 26: pembalikan atas pembalikan tidak diizinkan (jurnal % sudah pembalikan).', NEW.reversal_of_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_prevent_reverse_of_reversal
    BEFORE INSERT ON journal_entries
    FOR EACH ROW WHEN (NEW.reversal_of_id IS NOT NULL)
    EXECUTE FUNCTION cegah_pembalik_atas_pembalik();
