-- Benih SINTETIS gerbang V428 (bukan data pelanggan nyata). Tenant kaos (ada di salinan "Tenant").
INSERT INTO customers (id, tenant_id, nama, alamat, city, province, postal_code, negara) VALUES
 ('00000000-0000-4000-8000-000000000001', 'kaos-biru-konveksi', 'TES E2E Alamat saja', 'Jl. Contoh No. 1', NULL, NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000002', 'kaos-biru-konveksi', 'TES E2E Alamat+kota', 'Jl. Contoh 2', 'Manado', NULL, NULL, 'IDN'),
 ('00000000-0000-4000-8000-000000000003', 'kaos-biru-konveksi', 'TES E2E kota termuat', 'Jl. Contoh 3, Manado', 'Manado', NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000004', 'kaos-biru-konveksi', 'TES E2E lengkap', 'Jl. Contoh 4', 'Bitung', 'Sulawesi Utara', '95511', NULL),
 ('00000000-0000-4000-8000-000000000005', 'kaos-biru-konveksi', 'TES E2E kota saja', NULL, 'Tomohon', NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000006', 'kaos-biru-konveksi', 'TES E2E kosong', NULL, NULL, NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000007', 'kaos-biru-konveksi', 'TES E2E spasi', '   ', '', NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000008', 'kaos-biru-konveksi', 'TES E2E spasi tepi', '  Jl. Tepi 8  ', NULL, NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000009', 'kaos-biru-konveksi', 'TES E2E luar negeri', '1 Main St', 'Singapore', NULL, '018989', 'SGP'),
 ('00000000-0000-4000-8000-000000000010', 'kaos-biru-konveksi', 'TES E2E untuk diuji API', 'Jl. API 10', 'Manado', NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000011', 'kaos-biru-konveksi', 'TES E2E pelanggan baru', NULL, NULL, NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000012', 'kaos-biru-konveksi', 'TES E2E sumber gabung', 'Jl. Gabung 12', NULL, NULL, NULL, NULL),
 ('00000000-0000-4000-8000-000000000013', 'grapgrap-manado', 'TES E2E tenant lain', 'Jl. Lain 13', NULL, NULL, NULL, NULL);
INSERT INTO sales_orders (id, tenant_id, order_number, order_date, customer_id, customer_name, shipping_address) VALUES
 ('10000000-0000-4000-8000-000000000001', 'kaos-biru-konveksi', 'TES-SO-1', '2026-10-16', '00000000-0000-4000-8000-000000000001', 'x', 'Jl. Contoh No. 1'),
 ('10000000-0000-4000-8000-000000000002', 'kaos-biru-konveksi', 'TES-SO-2', '2026-10-16', '00000000-0000-4000-8000-000000000002', 'x', ' Jl. Contoh 2 '),
 ('10000000-0000-4000-8000-000000000003', 'kaos-biru-konveksi', 'TES-SO-3', '2026-10-16', '00000000-0000-4000-8000-000000000002', 'x', 'Gudang lain'),
 ('10000000-0000-4000-8000-000000000004', 'kaos-biru-konveksi', 'TES-SO-4', '2026-10-16', '00000000-0000-4000-8000-000000000006', 'x', NULL);
