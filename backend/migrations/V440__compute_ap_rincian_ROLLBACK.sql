-- ROLLBACK V440: hapus fungsi rincian (tak ada data/kolom; compute_ap_outstanding tak tersentuh oleh V440).
DROP FUNCTION IF EXISTS public.compute_ap_rincian(text);
