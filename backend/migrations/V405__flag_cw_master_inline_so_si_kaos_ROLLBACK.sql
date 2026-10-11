-- Rollback V405: cabut flag cakupan master inline SO/Faktur dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_master_inline_so_si';
