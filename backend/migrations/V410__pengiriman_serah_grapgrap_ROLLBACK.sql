-- Rollback V410: cabut serah dari grapgrap. Preferensi dulu (FK ke metode_serah). Cara serah yang SUDAH dipakai Surat Jalan
-- (pengiriman_serah.metode_serah_id) tak bisa dihapus (FK) -> rollback ini menolak keras; bila begitu, nonaktifkan
-- (aktif=false) secara manual sesudah putusan, jangan hapus jejak dokumen.
DELETE FROM pengiriman_preferensi WHERE tenant_id = 'grapgrap-manado';
DELETE FROM metode_serah WHERE tenant_id = 'grapgrap-manado';
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'pengiriman_serah';
