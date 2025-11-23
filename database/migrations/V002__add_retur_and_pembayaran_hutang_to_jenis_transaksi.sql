-- Migration: Add retur_penjualan, retur_pembelian, and pembayaran_hutang to jenis_transaksi constraint
-- Date: 2025-11-16
-- Description: Update chk_jenis_transaksi constraint to allow new transaction types

-- Step 1: Drop existing constraint
ALTER TABLE transaksi_harian 
DROP CONSTRAINT IF EXISTS chk_jenis_transaksi;

-- Step 2: Create new constraint with expanded allowed values
ALTER TABLE transaksi_harian 
ADD CONSTRAINT chk_jenis_transaksi 
CHECK (
    jenis_transaksi IN (
        'penjualan',
        'pembelian', 
        'beban',
        'retur_penjualan',
        'retur_pembelian',
        'pembayaran_hutang'
    )
);

-- Verify constraint
COMMENT ON CONSTRAINT chk_jenis_transaksi ON transaksi_harian IS 
'Allows: penjualan, pembelian, beban, retur_penjualan, retur_pembelian, pembayaran_hutang';

