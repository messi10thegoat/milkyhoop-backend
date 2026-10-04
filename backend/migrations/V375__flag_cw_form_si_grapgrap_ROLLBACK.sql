-- Rollback V375: cabut form + simpan Faktur CW dari grapgrap (form lama kembali).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature IN ('conversational_form_si', 'conversational_form_si_save');
