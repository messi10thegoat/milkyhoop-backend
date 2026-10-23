-- V455 (BACKEND2, disiapkan 23 Okt 2026; BELUM diizinkan) -- aturan (D) GRN, putusan LANGSUNG pemilik 23 Okt (pola SAP GR/IR):
-- urutan terima/tagih bebas. Tautan goods_receipt_bill_links mencatat JENIS pemasangan untuk aturan void simetris (dokumen LEBIH DULU tak
-- boleh di-void selama terpasang) dan laporan: 'terima_dulu' (tagihan memasang ke GRN; bawaan = seluruh baris lama), 'tagih_dulu'
-- (penerimaan memasang ke tagihan yang lebih dulu; penerimaan memposting selisih), 'legacy' (ditagih jalur lama sebelum flag; GRN tanpa stok).
-- ADITIF: kolom kind ber-DEFAULT + kolom nilai_tagih NULLABLE, baris lama = 'terima_dulu' (hari ini 0 baris). Nol jurnal. Jalankan -v ON_ERROR_STOP=1 -1.
ALTER TABLE goods_receipt_bill_links ADD COLUMN kind varchar(20) NOT NULL DEFAULT 'terima_dulu';
ALTER TABLE goods_receipt_bill_links ADD CONSTRAINT chk_grbl_kind CHECK (kind IN ('terima_dulu', 'tagih_dulu', 'legacy'));
-- nilai sisi TAGIHAN per tautan (B neto pro rata dari tagihan_aksi.neto_baris; review BACKEND 23 Okt): void membalik PERSIS tanpa hitung ulang.
-- NULL untuk baris lama (terima_dulu: B = grni_amount + variance_amount).
ALTER TABLE goods_receipt_bill_links ADD COLUMN nilai_tagih numeric(18,2);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'goods_receipt_bill_links' AND column_name = 'kind'
                   AND is_nullable = 'NO' AND column_default LIKE '%terima_dulu%') THEN
        RAISE EXCEPTION 'V455: kolom kind tak terpasang sesuai';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_grbl_kind') THEN
        RAISE EXCEPTION 'V455: CHECK chk_grbl_kind tak terpasang';
    END IF;
END $$;
