-- KELAS: tabel multi-tenant yang PK-nya TIDAK memuat tenant_id.
-- PK tanpa tenant_id pada tabel multi-tenant = menyatakan "unik GLOBAL lintas tenant".
-- Aman selama key-nya UUID acak; MEMATIKAN begitu key jadi deterministik/natural.
\pset border 2
WITH mt AS (   -- tabel yang punya kolom tenant_id
  SELECT c.table_name
  FROM information_schema.columns c
  JOIN information_schema.tables t
    ON t.table_name = c.table_name AND t.table_schema = 'public'
   AND t.table_type = 'BASE TABLE'
  WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'
),
pk AS (
  SELECT rel.relname AS table_name,
         con.conname AS pk_name,
         ARRAY(SELECT a.attname::text
                 FROM unnest(con.conkey) k
                 JOIN pg_attribute a ON a.attrelid = rel.oid AND a.attnum = k) AS pk_cols
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = rel.relnamespace AND n.nspname = 'public'
  WHERE con.contype = 'p'
)
SELECT pk.table_name AS tabel,
       pk.pk_name,
       array_to_string(pk.pk_cols, ', ') AS kolom_pk,
       CASE
         WHEN pk.pk_cols = ARRAY['id']::text[] THEN 'AMAN (surrogate id)'
         ELSE '⚠️ PERIKSA (natural/composite tanpa tenant)'
       END AS penilaian
FROM pk JOIN mt ON mt.table_name = pk.table_name
WHERE NOT ('tenant_id' = ANY(pk.pk_cols::text[]))
ORDER BY (pk.pk_cols = ARRAY['id']::text[]), pk.table_name;
