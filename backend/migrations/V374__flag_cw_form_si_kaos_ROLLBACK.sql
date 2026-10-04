-- Rollback V374: cabut flag form + simpan Faktur CW dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND feature IN ('conversational_form_si', 'conversational_form_si_save');
