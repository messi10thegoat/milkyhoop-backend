-- Rollback V408: cabut flag + cara serah awal kaos. Cara serah yang sudah dipakai Surat Jalan (FK RESTRICT) TIDAK
-- dihapus (dinonaktifkan saja) supaya snapshot dokumen tetap tertaut.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'pengiriman_serah';
UPDATE metode_serah SET aktif = false, updated_at = now()
 WHERE tenant_id = 'kaos-biru-konveksi'
   AND id IN (SELECT metode_serah_id FROM pengiriman_serah WHERE metode_serah_id IS NOT NULL);
DELETE FROM metode_serah
 WHERE tenant_id = 'kaos-biru-konveksi' AND nama IN ('Kurir', 'Antar sendiri', 'Ambil sendiri')
   AND id NOT IN (SELECT metode_serah_id FROM pengiriman_serah WHERE metode_serah_id IS NOT NULL)
   AND id NOT IN (SELECT default_metode_serah_id FROM pengiriman_preferensi WHERE default_metode_serah_id IS NOT NULL);
