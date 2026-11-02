"""U-BP-NOMOR (2 Nov 2026, disetujui MASTER): PERKIRAAN nomor jurnal berikutnya untuk pratinjau -- rumus PERSIS fungsi DB
penomoran jurnal (get_next_journal_number; argumen tenant, awalan, tanggal) TANPA menulis/mengunci. Modul ini TIDAK menerbitkan nomor:
  yymm   = to_char(COALESCE(p_date, tanggal_bisnis(tenant)), 'YYMM'); year/month dari tanggal yang sama
  actual = MAX sufiks angka journal_number '^<prefix>-<yymm>-[0-9]+$' di journal_entries tenant
  n      = GREATEST(COALESCE(journal_number_sequences.last_number, 0) + 1, actual + 1)   (baris baru: GREATEST(1, actual + 1) -- sama)
  nomor  = prefix || '-' || yymm || '-' || lpad(n, 4, '0')   (lpad SAMA, termasuk perilakunya di n >= 10000)
PERKIRAAN: penulis paralel bisa mengambilnya dulu (pola NetSuite; nomor final terbit saat simpan). Rumus ada di dua tempat (fungsi DB
inti vs SQL ini) -- kesetaraannya dibuktikan uji nyata (pratinjau == nomor terbit: penghitung ada / belum ada / tertinggal)."""
from datetime import date
from typing import Optional

SQL_PERKIRAAN_NOMOR_JURNAL = """
WITH t AS (SELECT COALESCE($3::date, tanggal_bisnis($1)) AS d),
     k AS (SELECT to_char(t.d, 'YYMM') AS yymm, EXTRACT(YEAR FROM t.d)::int AS y, EXTRACT(MONTH FROM t.d)::int AS m FROM t),
     a AS (SELECT COALESCE(MAX((regexp_match(je.journal_number, '^' || $2 || '-' || k.yymm || '-([0-9]+)$'))[1]::int), 0) AS actual
             FROM journal_entries je, k
            WHERE je.tenant_id = $1 AND je.journal_number ~ ('^' || $2 || '-' || k.yymm || '-[0-9]+$'))
SELECT $2 || '-' || k.yymm || '-' || lpad(GREATEST(COALESCE(s.last_number, 0) + 1, a.actual + 1)::text, 4, '0')
  FROM k CROSS JOIN a
  LEFT JOIN journal_number_sequences s ON s.tenant_id = $1 AND s.prefix = $2 AND s.year = k.y AND s.month = k.m
"""


async def perkiraan_nomor_jurnal(conn, tenant_id: str, prefix: str, tanggal: Optional[date] = None) -> str:
    """Nomor yang AKAN diberikan fungsi DB penomoran jurnal untuk (tenant, awalan, tanggal) -- dibaca saja (SELECT), tanpa kunci."""
    return await conn.fetchval(SQL_PERKIRAAN_NOMOR_JURNAL, tenant_id, prefix, tanggal)
