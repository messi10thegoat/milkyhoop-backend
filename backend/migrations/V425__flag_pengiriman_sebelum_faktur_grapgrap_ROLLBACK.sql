-- ROLLBACK V425: matikan R1 non-stok di grapgrap (SJ-so yang sudah ada TIDAK dihapus; void lewat /deliveries/{id}/void).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'pengiriman_sebelum_faktur';
