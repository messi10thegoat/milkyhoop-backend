-- V513: SERIAL S1 (DESAIN-serial-20261127; Q1-Q3 = ya, Q4 migrasi GO pemilik LANGSUNG 27 Nov ±09:00 via MASTER). ADITIF, 0 data (prod 27 Nov: 0 item_serials).
-- (1) taut baris kartu stok <-> nomor seri: dasar jejak, pemilihan keluar, dan pembalikan void. (2) status item_serials dibatasi.
-- (3) nomor seri di baris DRAF Penyesuaian (GO pemilik LANGSUNG 27 Nov opsi (a): kolom aditif saja), dinilai saat posting.
-- Pembalik: V513__serial_taut_ledger_ROLLBACK.sql. Tanpa BEGIN/COMMIT (apply_mig).
CREATE TABLE inventory_ledger_serials (
    ledger_id  UUID NOT NULL REFERENCES inventory_ledger(id),
    serial_id  UUID NOT NULL REFERENCES item_serials(id),
    tenant_id  TEXT NOT NULL,
    arah       TEXT NOT NULL CONSTRAINT chk_ils_arah CHECK (arah IN ('masuk', 'keluar')),
    status_sebelum TEXT,
    gudang_sebelum UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ledger_id, serial_id)
);
CREATE INDEX idx_ils_serial ON inventory_ledger_serials (tenant_id, serial_id);

-- CHECK lama V048 chk_is_status (available/reserved/sold/returned/damaged/scrapped) diganti SUPERSET (+ transit, returned_vendor, adjusted_out).
ALTER TABLE item_serials DROP CONSTRAINT chk_is_status;
ALTER TABLE item_serials ADD CONSTRAINT chk_item_serials_status
    CHECK (status IN ('available', 'reserved', 'transit', 'sold', 'returned', 'returned_vendor', 'adjusted_out', 'damaged', 'scrapped'));

ALTER TABLE stock_adjustment_items ADD COLUMN serials TEXT[];
