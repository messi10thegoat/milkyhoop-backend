-- V334 (MASTER/FRONTEND 30 Sep 2026) — modul CW kasbon:
--   (1) nomor dokumen kasbon KSB-YYMM-NNNN (pola DEP: urutan per tenant x bulan tanggal bisnis);
--   (2) isi-balik nomor kasbon yang sudah ada (IZIN PEMILIK LANGSUNG di sesi BACKEND 30 Sep: grapgrap 4 + kaos 1),
--       bulan = tanggal_bisnis(tenant, created_at) (zona waktu tenant), urut created_at;
--   (3) flag conversational_kasbon_page KAOS SAJA.
-- Hanya kolom metadata + urutan: NOL jurnal / saldo / mutasi bank berubah.

ALTER TABLE employee_advances ADD COLUMN IF NOT EXISTS advance_number varchar(30);

CREATE TABLE IF NOT EXISTS employee_advance_sequences (
    tenant_id   text        NOT NULL,
    year_month  varchar(7)  NOT NULL,
    last_number integer     NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, year_month)
);

CREATE OR REPLACE FUNCTION generate_employee_advance_number(p_tenant_id text)
RETURNS varchar LANGUAGE plpgsql AS $$
DECLARE
    v_tgl date := tanggal_bisnis(p_tenant_id);
    v_n   integer;
BEGIN
    INSERT INTO employee_advance_sequences (tenant_id, year_month, last_number)
    VALUES (p_tenant_id, to_char(v_tgl, 'YYYY-MM'), 1)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = employee_advance_sequences.last_number + 1, updated_at = now()
    RETURNING last_number INTO v_n;
    -- Format: KSB-YYMM-0001
    RETURN 'KSB-' || to_char(v_tgl, 'YYMM') || '-' || lpad(v_n::text, 4, '0');
END;
$$;

-- (2) isi-balik
WITH b AS (
    SELECT id,
           to_char(tanggal_bisnis(tenant_id, created_at), 'YYMM') AS yymm,
           row_number() OVER (PARTITION BY tenant_id, to_char(tanggal_bisnis(tenant_id, created_at), 'YYYY-MM')
                              ORDER BY created_at, id) AS n
    FROM employee_advances
    WHERE advance_number IS NULL
)
UPDATE employee_advances a
SET advance_number = 'KSB-' || b.yymm || '-' || lpad(b.n::text, 4, '0')
FROM b WHERE a.id = b.id;

INSERT INTO employee_advance_sequences (tenant_id, year_month, last_number)
SELECT tenant_id, to_char(tanggal_bisnis(tenant_id, created_at), 'YYYY-MM'), count(*)
FROM employee_advances GROUP BY 1, 2
ON CONFLICT (tenant_id, year_month)
DO UPDATE SET last_number = GREATEST(employee_advance_sequences.last_number, EXCLUDED.last_number), updated_at = now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_advances_tenant_number
    ON employee_advances (tenant_id, advance_number) WHERE advance_number IS NOT NULL;

-- (3) flag
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_kasbon_page'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM employee_advances WHERE advance_number IS NULL) THEN
        RAISE EXCEPTION 'V334: masih ada kasbon tanpa nomor';
    END IF;
    IF EXISTS (SELECT 1 FROM employee_advances a
               WHERE (SELECT last_number FROM employee_advance_sequences s
                      WHERE s.tenant_id = a.tenant_id
                        AND s.year_month = to_char(tanggal_bisnis(a.tenant_id, a.created_at), 'YYYY-MM'))
                     < split_part(a.advance_number, '-', 3)::int) THEN
        RAISE EXCEPTION 'V334: urutan di bawah nomor terpakai (nomor berikut akan bentrok)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_kasbon_page' AND enabled) THEN
        RAISE EXCEPTION 'V334: flag conversational_kasbon_page kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_kasbon_page') THEN
        RAISE EXCEPTION 'V334: flag conversational_kasbon_page ditemukan di tenant lain';
    END IF;
END $$;
