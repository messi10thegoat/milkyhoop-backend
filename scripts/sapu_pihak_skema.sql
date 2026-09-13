-- Sapuan kelas uuid-vs-varchar pihak — SKEMA + DATA. Baca-saja; kontrol sampah di ROLLBACK.
\set ON_ERROR_STOP 1
\echo '== 1. kolom pihak per tabel (information_schema, BASE TABLE, public)'
SELECT c.data_type, c.table_name, c.column_name
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
  AND c.column_name ~ '^(customer|vendor|supplier|contact|partner|pelanggan|pemasok)_id$'
ORDER BY c.column_name, c.data_type, c.table_name;

\echo '== ringkas: per nama kolom, cacah tabel per tipe'
SELECT c.column_name, c.data_type, count(*) AS tabel
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
  AND c.column_name ~ '^(customer|vendor|supplier|contact|partner|pelanggan|pemasok)_id$'
GROUP BY 1,2 ORDER BY 1,2;

\echo '== tabel INDUK pihak: tipe id'
SELECT table_name, column_name, data_type FROM information_schema.columns
WHERE table_schema='public' AND table_name IN ('customers','vendors','suppliers','contacts') AND column_name='id';

\echo '== 4. baris varchar pihak yang GAGAL diparse UUID, per tabel (dibangkitkan dinamis)'
BEGIN;
CREATE OR REPLACE FUNCTION pg_temp.bisa_uuid(v text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT btrim(v) ~* '^\{?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\}?$' $$;
CREATE TEMP TABLE hasil_parse (tabel text, kolom text, total bigint, kosong bigint, bukan_uuid bigint, huruf_besar_spasi bigint, contoh text);
DO $$
DECLARE r record; q text;
BEGIN
  FOR r IN
    SELECT c.table_name, c.column_name FROM information_schema.columns c
    JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
    WHERE c.table_schema='public' AND t.table_type='BASE TABLE'
      AND c.column_name ~ '^(customer|vendor|supplier|contact|partner|pelanggan|pemasok)_id$'
      AND c.data_type IN ('character varying','text')
  LOOP
    q := format($f$INSERT INTO hasil_parse
      SELECT %L, %L, count(*),
             count(*) FILTER (WHERE %2$I IS NULL OR btrim(%2$I) = ''),
             count(*) FILTER (WHERE %2$I IS NOT NULL AND btrim(%2$I) <> '' AND NOT pg_temp.bisa_uuid(%2$I)),
             count(*) FILTER (WHERE pg_temp.bisa_uuid(%2$I) AND %2$I <> lower(btrim(%2$I))),
             (SELECT string_agg(DISTINCT left(%2$I, 40), ' | ') FROM (SELECT %2$I FROM %1$I WHERE %2$I IS NOT NULL AND btrim(%2$I) <> '' AND NOT pg_temp.bisa_uuid(%2$I) LIMIT 5) s)
      FROM %1$I$f$, r.table_name, r.column_name);
    EXECUTE q;
  END LOOP;
END $$;
SELECT * FROM hasil_parse WHERE total > 0 ORDER BY bukan_uuid DESC, tabel;
SELECT count(*) AS kolom_varchar_diperiksa FROM hasil_parse;

\echo '== KONTROL: sisip sampah ke credit_notes.customer_id -> harus tertangkap (+1)'
SAVEPOINT k;
SELECT bukan_uuid AS sebelum FROM hasil_parse WHERE tabel='credit_notes' AND kolom='customer_id' \gset
UPDATE credit_notes SET customer_id = 'SAMPAH-KONTROL' WHERE id = (SELECT id FROM credit_notes WHERE status='draft' ORDER BY id LIMIT 1);
SELECT count(*) FILTER (WHERE customer_id IS NOT NULL AND btrim(customer_id) <> '' AND NOT pg_temp.bisa_uuid(customer_id)) AS sesudah FROM credit_notes \gset
ROLLBACK TO SAVEPOINT k;
\echo 'kontrol: sebelum=' :sebelum ' sesudah=' :sesudah ' (harap sesudah = sebelum + 1)'
ROLLBACK;

\echo '== nota kredit posted bukan-UUID: nilai, pembuat, asal'
SELECT cn.id, cn.credit_note_number, cn.status, cn.customer_id, cn.customer_name, cn.created_at, cn.created_by,
       (SELECT count(*) FROM customers c WHERE c.id::text = cn.customer_id) AS cocok_id_customers,
       (SELECT string_agg(c.id::text, ',') FROM customers c WHERE c.tenant_id = cn.tenant_id AND c.nama = cn.customer_name) AS id_pelanggan_sesuai_nama
FROM credit_notes cn
WHERE cn.status = 'posted' AND cn.customer_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
