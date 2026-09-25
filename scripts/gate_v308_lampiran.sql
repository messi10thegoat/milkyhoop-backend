-- Gerbang V308 (#30): semantik trigger lepas_tautan_dokumen_entitas di DB scratch.
\set ON_ERROR_STOP on
DO $$
DECLARE
  t1 text := (SELECT id FROM "Tenant" ORDER BY id LIMIT 1);
  t2 text := (SELECT id FROM "Tenant" WHERE id <> (SELECT id FROM "Tenant" ORDER BY id LIMIT 1) ORDER BY id LIMIT 1);
  ca uuid := gen_random_uuid(); cb uuid := gen_random_uuid(); v uuid := gen_random_uuid();
  d1 uuid := gen_random_uuid(); d2 uuid := gen_random_uuid(); d3 uuid := gen_random_uuid();
  n int; jml_trig int;
BEGIN
  IF t2 IS NULL THEN RAISE EXCEPTION 'GAGAL: butuh 2 tenant'; END IF;
  SELECT count(*) INTO jml_trig FROM pg_trigger WHERE tgname = 'trg_lepas_dokumen' AND NOT tgisinternal;
  IF jml_trig <> 21 THEN RAISE EXCEPTION 'GAGAL: trigger terpasang % (harus 21)', jml_trig; END IF;

  INSERT INTO customers (id, tenant_id, nama) VALUES (ca, t1, 'GATE A'), (cb, t1, 'GATE B');
  INSERT INTO vendors (id, tenant_id, name) VALUES (v, t1, 'GATE V');
  INSERT INTO documents (id, tenant_id, file_path, file_name) VALUES (d1, t1, 'gate/1', '1'), (d2, t1, 'gate/2', '2'),
    (d3, t2, 'gate/3', '3');
  INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id) VALUES
    (t1, d1, 'customer', ca),      -- harus LEPAS
    (t1, d2, 'customer', ca),      -- harus LEPAS
    (t1, d1, 'vendor', v),         -- entitas lain: TETAP
    (t1, d1, 'customer', cb),      -- customer lain: TETAP
    (t2, d3, 'customer', ca),      -- tenant lain (id sama): TETAP
    (t1, d2, 'vendor', ca);        -- jenis lain (id sama): TETAP

  UPDATE customers SET deleted_at = now() WHERE id = cb;   -- hapus-LUNAK: tak memicu
  DELETE FROM customers WHERE id = ca;

  SELECT count(*) INTO n FROM document_attachments WHERE entity_type = 'customer' AND entity_id = ca AND tenant_id = t1;
  IF n <> 0 THEN RAISE EXCEPTION 'GAGAL: tautan entitas terhapus masih % (harus 0)', n; END IF;
  SELECT count(*) INTO n FROM document_attachments WHERE document_id IN (d1, d2, d3);
  IF n <> 4 THEN RAISE EXCEPTION 'GAGAL: tautan lain tersisa % (harus 4: vendor, customer B, tenant lain, jenis lain)', n; END IF;
  SELECT count(*) INTO n FROM document_attachments WHERE tenant_id = t2 AND entity_id = ca;
  IF n <> 1 THEN RAISE EXCEPTION 'GAGAL: tautan tenant lain ikut terhapus'; END IF;
  SELECT count(*) INTO n FROM document_attachments WHERE entity_type = 'vendor' AND entity_id = ca;
  IF n <> 1 THEN RAISE EXCEPTION 'GAGAL: tautan jenis lain ikut terhapus'; END IF;
  SELECT count(*) INTO n FROM documents WHERE id IN (d1, d2, d3);
  IF n <> 3 THEN RAISE EXCEPTION 'GAGAL: baris documents ikut terhapus (%)', n; END IF;

  DELETE FROM vendors WHERE id = v;
  SELECT count(*) INTO n FROM document_attachments WHERE entity_type = 'vendor' AND entity_id = v;
  IF n <> 0 THEN RAISE EXCEPTION 'GAGAL: tautan vendor terhapus masih %', n; END IF;
  RAISE NOTICE 'LULUS: V308 lepas tautan (hapus-keras), pagar tenant/jenis/entitas, dokumen utuh, 21 trigger';
END $$;
