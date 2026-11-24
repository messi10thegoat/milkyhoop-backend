-- Pembalik V509. Data opname (header+baris+nomor urut) HILANG; Penyesuaian Stok hasil posting TETAP (dokumen SA berdiri sendiri). Trigger penjaga dilepas.
DROP TRIGGER IF EXISTS trg_gudang_terkunci ON inventory_ledger;
DROP FUNCTION IF EXISTS trg_tolak_gerak_gudang_terkunci();
DROP TABLE IF EXISTS stock_opname_lines;
DROP TABLE IF EXISTS stock_opnames;
DROP FUNCTION IF EXISTS generate_stock_opname_number(TEXT, DATE);
DROP TABLE IF EXISTS stock_opname_sequences;
