-- V407 (BACKEND2 11 Okt 2026; kontrak backend/docs/KONTRAK-pengiriman-serah-20261011.md, disetujui MASTER) --
-- serah-terima Surat Jalan: cara serah (daftar per tenant), alamat kirim, resi BANYAK, preferensi bawaan.
-- ADITIF MURNI: empat tabel baru; NOL perubahan pada tabel lama (invoice_fulfillments sengaja TIDAK di-ALTER:
-- trigger trg_so_shipped_recompute AFTER UPDATE per baris akan terpancing tiap UPDATE metadata). Nol jurnal/stok/HPP.
-- Terapkan HANYA lewat /root/apply_mig.sh (psql -v ON_ERROR_STOP=1 -1 = satu transaksi).
-- Flag + seed kaos = V408 (terpisah, sesudah kode hidup).
CREATE TABLE metode_serah (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   text        NOT NULL,
    nama        varchar(100) NOT NULL,
    jenis       varchar(20) NOT NULL,
    aktif       boolean     NOT NULL DEFAULT true,
    urutan      integer     NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid,
    CONSTRAINT metode_serah_jenis_chk CHECK (jenis IN ('kurir', 'antar_sendiri', 'ambil_sendiri')),
    CONSTRAINT metode_serah_nama_chk  CHECK (btrim(nama) <> '')
);
CREATE UNIQUE INDEX uq_metode_serah_tenant_nama ON metode_serah (tenant_id, lower(nama));
CREATE UNIQUE INDEX uq_metode_serah_id_tenant   ON metode_serah (id, tenant_id);  -- sasaran FK komposit (tenant tak bisa silang)

CREATE TABLE pengiriman_serah (
    fulfillment_id  uuid PRIMARY KEY REFERENCES invoice_fulfillments (id),
    tenant_id       text        NOT NULL,
    metode_serah_id uuid,
    metode_nama     text,                -- SNAPSHOT: setelan diubah tak menyentuh dokumen lama
    jenis_serah     varchar(20),         -- SNAPSHOT
    kirim_ke        text,
    kirim_ke_sumber varchar(10),
    diterima_pada   date,
    created_at      timestamptz NOT NULL DEFAULT now(),
    diubah_at       timestamptz NOT NULL DEFAULT now(),
    diubah_oleh     uuid,
    CONSTRAINT pengiriman_serah_metode_fk FOREIGN KEY (metode_serah_id, tenant_id) REFERENCES metode_serah (id, tenant_id),
    CONSTRAINT pengiriman_serah_jenis_chk  CHECK (jenis_serah IS NULL OR jenis_serah IN ('kurir', 'antar_sendiri', 'ambil_sendiri')),
    CONSTRAINT pengiriman_serah_sumber_chk CHECK (kirim_ke_sumber IS NULL OR kirim_ke_sumber IN ('pesanan', 'pelanggan', 'manual')),
    CONSTRAINT pengiriman_serah_ambil_chk  CHECK (jenis_serah IS DISTINCT FROM 'ambil_sendiri' OR kirim_ke IS NULL)
);
CREATE INDEX idx_pengiriman_serah_tenant ON pengiriman_serah (tenant_id);

CREATE TABLE pengiriman_resi (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      text        NOT NULL,
    fulfillment_id uuid        NOT NULL REFERENCES invoice_fulfillments (id),
    nomor_resi     varchar(100) NOT NULL,
    kurir_nama     varchar(100),
    urutan         smallint    NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pengiriman_resi_nomor_chk CHECK (btrim(nomor_resi) <> '')
);
CREATE UNIQUE INDEX uq_pengiriman_resi_nomor ON pengiriman_resi (fulfillment_id, lower(nomor_resi));
CREATE INDEX idx_pengiriman_resi_tenant ON pengiriman_resi (tenant_id, fulfillment_id);

-- Preferensi bawaan per tenant. Tabel sendiri (bukan kolom accounting_settings): terukur 11 Okt 5 dari 6 tenant tak punya
-- baris accounting_settings dan GET-nya sengaja baca-saja; response_model bersama -> kunci baru akan muncul di semua tenant.
CREATE TABLE pengiriman_preferensi (
    tenant_id               text PRIMARY KEY,
    default_lokasi_kirim_id uuid REFERENCES warehouses (id),
    default_metode_serah_id uuid,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    updated_by              uuid,
    CONSTRAINT pengiriman_pref_metode_fk FOREIGN KEY (default_metode_serah_id, tenant_id) REFERENCES metode_serah (id, tenant_id)
);
