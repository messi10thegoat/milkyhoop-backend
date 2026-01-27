#!/usr/bin/env python3
import re

# Read the file
with open('/root/milkyhoop/frontend/web/src/components/app/MoreModules/index.tsx', 'r') as f:
    content = f.read()

# 1. Add Saldo Awal to AKUNTANSI section (after Pusat Laba)
content = content.replace(
    "{ id: 'pusat_laba', label: 'Pusat Laba', icon: <ProfitCenterIcon />, tier: 'tier3' },\n    ],\n  },\n  {\n    title: 'PERBANKAN',",
    "{ id: 'pusat_laba', label: 'Pusat Laba', icon: <ProfitCenterIcon />, tier: 'tier3' },\n      { id: 'saldo_awal', label: 'Saldo Awal', icon: <OpeningBalanceIcon />, tier: 'core' },\n    ],\n  },\n  {\n    title: 'PERBANKAN',"
)

# 2. Add Surat Jalan to PENJUALAN section (after Uang Muka Pelanggan)
content = content.replace(
    "{ id: 'uang_muka_pelanggan', label: 'Uang Muka Pelanggan', icon: <CustomerAdvanceIcon />, tier: 'core' },\n    ],\n  },\n  {\n    title: 'PEMBELIAN',",
    "{ id: 'uang_muka_pelanggan', label: 'Uang Muka Pelanggan', icon: <CustomerAdvanceIcon />, tier: 'core' },\n      { id: 'surat_jalan', label: 'Surat Jalan', icon: <ShipmentIcon />, tier: 'core' },\n    ],\n  },\n  {\n    title: 'PEMBELIAN',"
)

# 3. Add Penerimaan Barang to PEMBELIAN section (after Uang Muka Pemasok)
content = content.replace(
    "{ id: 'uang_muka_pemasok', label: 'Uang Muka Pemasok', icon: <VendorAdvanceIcon />, tier: 'tier1' },\n    ],\n  },\n  {\n    title: 'PAJAK',",
    "{ id: 'uang_muka_pemasok', label: 'Uang Muka Pemasok', icon: <VendorAdvanceIcon />, tier: 'tier1' },\n      { id: 'penerimaan_barang', label: 'Penerimaan Barang', icon: <GoodsReceiptIcon />, tier: 'core' },\n    ],\n  },\n  {\n    title: 'PAJAK',"
)

# 4. Add Serial Number to PELACAKAN LANJUTAN section (after Barang Terkendali)
content = content.replace(
    "{ id: 'barang_terkendali', label: 'Barang Terkendali', icon: <ControlledSubstanceIcon />, tier: 'core' },\n    ],\n  },\n  {\n    title: 'LAPORAN',",
    "{ id: 'barang_terkendali', label: 'Barang Terkendali', icon: <ControlledSubstanceIcon />, tier: 'core' },\n      { id: 'serial_number', label: 'Serial Number', icon: <SerialNumberIcon />, tier: 'tier1' },\n    ],\n  },\n  {\n    title: 'LAPORAN',"
)

# 5. Add 3 new categories after PENGATURAN section
new_categories = '''
  {
    title: 'MANUFAKTUR',
    items: [
      { id: 'bom', label: 'Bill of Materials', icon: <BOMIcon />, tier: 'industry' },
      { id: 'perintah_produksi', label: 'Perintah Produksi', icon: <ProductionOrderIcon />, tier: 'industry' },
      { id: 'work_centers', label: 'Work Centers', icon: <WorkCenterIcon />, tier: 'industry' },
      { id: 'analisis_varians', label: 'Analisis Varians', icon: <VarianceAnalysisIcon />, tier: 'industry' },
    ],
  },
  {
    title: 'F&B / RESTORAN',
    items: [
      { id: 'resep_costing', label: 'Resep & Costing', icon: <RecipeIcon />, tier: 'industry' },
      { id: 'kitchen_display', label: 'Kitchen Display', icon: <KDSIcon />, tier: 'industry' },
      { id: 'manajemen_meja', label: 'Manajemen Meja', icon: <TableManagementIcon />, tier: 'industry' },
      { id: 'reservasi', label: 'Reservasi', icon: <ReservationIcon />, tier: 'industry' },
    ],
  },
  {
    title: 'PROYEK & WAKTU',
    items: [
      { id: 'proyek', label: 'Proyek', icon: <ProjectIcon />, tier: 'industry' },
      { id: 'timesheet', label: 'Timesheet', icon: <TimesheetIcon />, tier: 'industry' },
      { id: 'timer', label: 'Timer', icon: <TimerIcon />, tier: 'industry' },
    ],
  },'''

content = content.replace(
    "{\n    title: 'PENGATURAN',\n    items: [\n      { id: 'mata_uang', label: 'Mata Uang', icon: <CurrencyIcon />, tier: 'core' },\n      { id: 'pengaturan_umum', label: 'Pengaturan Umum', icon: <SettingsIcon />, tier: 'core' },\n    ],\n  },\n];",
    "{\n    title: 'PENGATURAN',\n    items: [\n      { id: 'mata_uang', label: 'Mata Uang', icon: <CurrencyIcon />, tier: 'core' },\n      { id: 'pengaturan_umum', label: 'Pengaturan Umum', icon: <SettingsIcon />, tier: 'core' },\n    ],\n  }," + new_categories + "\n];"
)

# 6. Update tier badge styling to handle 'industry' tier
old_badge = '''                {/* Tier Badge */}
                {item.tier && item.tier !== 'core' && (
                  <span
                    style={{
                      fontSize: '10px',
                      fontWeight: 600,
                      color: item.tier === 'tier1' ? '#6B7280' : item.tier === 'tier2' ? '#9333EA' : '#DC2626',
                      backgroundColor: item.tier === 'tier1' ? '#F3F4F6' : item.tier === 'tier2' ? '#F3E8FF' : '#FEE2E2',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      marginRight: '8px',
                      textTransform: 'uppercase',
                    }}
                  >
                    {item.tier === 'tier1' ? 'Pro' : item.tier === 'tier2' ? 'Enterprise' : 'Corporate'}
                  </span>
                )}'''

new_badge = '''                {/* Tier Badge */}
                {item.tier && item.tier !== 'core' && (
                  <span
                    style={{
                      fontSize: '10px',
                      fontWeight: 600,
                      color: item.tier === 'tier1' ? '#6B7280' : item.tier === 'tier2' ? '#9333EA' : item.tier === 'tier3' ? '#DC2626' : '#0891B2',
                      backgroundColor: item.tier === 'tier1' ? '#F3F4F6' : item.tier === 'tier2' ? '#F3E8FF' : item.tier === 'tier3' ? '#FEE2E2' : '#ECFEFF',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      marginRight: '8px',
                      textTransform: 'uppercase',
                    }}
                  >
                    {item.tier === 'tier1' ? 'Pro' : item.tier === 'tier2' ? 'Enterprise' : item.tier === 'tier3' ? 'Corporate' : 'Industry'}
                  </span>
                )}'''

content = content.replace(old_badge, new_badge)

# Write the file
with open('/root/milkyhoop/frontend/web/src/components/app/MoreModules/index.tsx', 'w') as f:
    f.write(content)

print('Modules updated successfully!')
print('Added: Saldo Awal, Surat Jalan, Penerimaan Barang, Serial Number')
print('Added categories: MANUFAKTUR, F&B / RESTORAN, PROYEK & WAKTU')
print('Updated tier badge styling for industry tier')
