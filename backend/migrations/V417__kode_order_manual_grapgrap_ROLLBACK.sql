-- Rollback V417: grapgrap kembali menerbitkan kode order otomatis saat SO dikonfirmasi.
UPDATE order_code_settings SET trigger = 'so_confirmed', updated_at = now() WHERE tenant_id = 'grapgrap-manado' AND trigger = 'manual_only';
