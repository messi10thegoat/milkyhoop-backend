-- ROLLBACK V430: hapus fungsi rincian (tak ada data/kolom; compute_ar_outstanding tak tersentuh oleh V430).
DROP FUNCTION IF EXISTS public.compute_ar_rincian(text);
