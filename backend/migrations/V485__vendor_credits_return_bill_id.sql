-- V485 (BACKEND2 7 Nov 2026; BUG-069; konfirmasi LANGSUNG pemilik di sesi BACKEND2 7 Nov malam; terapkan lewat /root/apply_mig.sh)
-- vendor_credits.original_bill_id membawa DUA makna: (1) atribusi hutang (compute_ap_outstanding cabang 4 V441; terapkan/lepas
-- menyetel/menghapusnya) dan (2) SUMBER retur (batas qty retur, gudang asal, WAC, catatan "sudah diretur"). Retur dari faktur yang
-- sudah LUNAS butuh (2) tanpa (1) -> nota debit BEBAS = saldo kredit vendor (cabang 5), bisa diterapkan ke tagihan berikut atau
-- diterima tunai (jalur yang ada). Kolom baru return_bill_id/return_bill_number = SUMBER retur, terpisah dari atribusi.
-- Efek buku: NOL jurnal, NOL perubahan angka (compute_ap tak membaca kolom baru). Backfill = hanya menambah rujukan pada ND yang
-- dibuat DARI faktur (alasan bukan potongan), BUKAN ND bebas yang kelak diterapkan (punya baris vendor_credit_applications).
-- Trigger trg_vendor_credit_kaitan_tagihan_beku hanya mengawasi original_bill_id -> tak tersentuh.
ALTER TABLE vendor_credits ADD COLUMN IF NOT EXISTS return_bill_id UUID NULL;
ALTER TABLE vendor_credits ADD COLUMN IF NOT EXISTS return_bill_number VARCHAR(50) NULL;
CREATE INDEX IF NOT EXISTS idx_vendor_credits_return_bill ON vendor_credits (tenant_id, return_bill_id) WHERE return_bill_id IS NOT NULL;
UPDATE vendor_credits vc
   SET return_bill_id = vc.original_bill_id, return_bill_number = vc.original_bill_number
 WHERE vc.original_bill_id IS NOT NULL AND vc.return_bill_id IS NULL
   AND COALESCE(vc.reason, '') NOT IN ('discount', 'pricing_error')
   AND NOT EXISTS (SELECT 1 FROM vendor_credit_applications vca
                    WHERE vca.vendor_credit_id = vc.id AND vca.bill_id = vc.original_bill_id);
DO $$
DECLARE kurang INT; lebih INT;
BEGIN
    SELECT count(*) INTO kurang FROM vendor_credits vc
     WHERE vc.original_bill_id IS NOT NULL AND vc.return_bill_id IS NULL AND COALESCE(vc.reason, '') NOT IN ('discount', 'pricing_error')
       AND NOT EXISTS (SELECT 1 FROM vendor_credit_applications vca WHERE vca.vendor_credit_id = vc.id AND vca.bill_id = vc.original_bill_id);
    SELECT count(*) INTO lebih FROM vendor_credits WHERE return_bill_id IS NOT NULL AND return_bill_id IS DISTINCT FROM original_bill_id;
    IF kurang > 0 THEN RAISE EXCEPTION 'V485: % nota debit retur bertaut belum terisi return_bill_id', kurang; END IF;
    IF lebih > 0 THEN RAISE EXCEPTION 'V485: % baris return_bill_id beda dari original_bill_id saat backfill', lebih; END IF;
END $$;
