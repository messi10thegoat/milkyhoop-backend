-- Pembalik V512 (pagar induk varian): buang 15 trigger + fungsi. Aman kapan saja (tak ada data yang diubah V512).
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT DISTINCT event_object_table AS t FROM information_schema.triggers
           WHERE trigger_schema = 'public' AND trigger_name = 'trg_tolak_induk_varian' LOOP
    EXECUTE format('DROP TRIGGER trg_tolak_induk_varian ON %I', r.t);
  END LOOP;
END $$;
DROP FUNCTION IF EXISTS tolak_induk_varian_di_dokumen();
DELETE FROM schema_migrations WHERE version = 'V512__pagar_induk_varian.sql';
