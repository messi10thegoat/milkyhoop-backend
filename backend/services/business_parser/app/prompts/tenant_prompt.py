"""
Tenant query classification prompt template

This prompt is used by the LLM layer to classify tenant business queries
and extract relevant entities.
"""

TENANT_PROMPT_TEMPLATE = """Classify tenant business query into intent + extract entities.

INTENTS:
1. transaction_record - Jual/beli/bayar transactions (complete OR incomplete). Examples:
   - Complete: "jual 100 kaos @45rb", "beli 50 buku @10rb tunai"
   - Incomplete: "jual kopi", "beli kain", "bayar gaji" (missing details - will be asked later)
   - Generic: Works for ANY product/service (barang: kaos, buku, kopi, kain, dll; jasa: konsultasi, desain, servis, dll)
2. retur_penjualan - Customer return barang rusak/salah (e.g. "customer return 3 pcs kemeja rusak")
3. retur_pembelian - Return ke supplier (e.g. "return 5 buku ke supplier")
4. pembayaran_hutang - Bayar cicilan/pelunasan hutang (e.g. "bayar cicilan Rp5jt dari total Rp20jt")
5. financial_report - Laporan laba/rugi/neraca/kas + Salary payment queries (e.g. "untung bulan ini?", "sudah bayar gaji siapa saja?", "gaji bulan November sudah bayar siapa saja?")
6. top_products - Terlaris/best seller (e.g. "produk paling laku?")
7. low_sell_products - Produk kurang laku (e.g. "produk mana yang kurang laku?")
8. query_transaksi - Filter transaksi by supplier/date (e.g. "transaksi supplier Toko Kain bulan Oktober")
9. inventory_query - Cek stok (e.g. "stok ballpoint berapa?")
10. inventory_history - Riwayat pergerakan stok (e.g. "riwayat stok kain cotton", "history stok ballpoint bulan lalu")
11. koreksi - Koreksi/update transaksi sebelumnya (e.g. "ada koreksi", "salah harusnya Rp500rb", "ubah nominal jadi Rp1jt")
12. general_inquiry - Tips/strategi bisnis
13. out_of_scope - Bukan bisnis

ENTITIES BY INTENT:

transaction_record:
{{
  "jenis_transaksi": "penjualan|pembelian|beban|modal|prive",
  "total_nominal": 450000,  // OPTIONAL for incomplete: null if missing. If complete: MUST BE jumlah × harga_satuan
  "metode_pembayaran": "cash|transfer|tempo",  // OPTIONAL: null if missing
  "status_pembayaran": "lunas|dp|tempo",  // OPTIONAL
  "kategori_beban": "beban_gaji|beban_operasional|beban_pajak|beban_listrik|beban_sewa|bahan_baku",  // For beban transactions
  "detail_karyawan": "Anna, Bambang, Inggrid",  // For beban_gaji: extract employee names (comma-separated if multiple)
  "periode_gaji": "November",  // For beban_gaji: extract period (e.g. "November", "Desember", "bulan ini")
  "items": [{{"nama_produk": "x", "jumlah": 10, "satuan": "pcs", "harga_satuan": 45000, "subtotal": 450000}}],  // OPTIONAL: [] if incomplete. If complete: ALWAYS calculate subtotal = jumlah × harga_satuan
  "discount_type": "percentage|nominal",  // OPTIONAL: "percentage" for %, "nominal" for fixed amount
  "discount_value": 10,  // OPTIONAL: discount amount (10 = 10% if percentage, 10000 = Rp10rb if nominal)
  "include_vat": false,  // OPTIONAL: true if "ppn", "pajak", "vat", "+ppn" mentioned
  "inventory_impact": {{
    "is_tracked": true,
    "jenis_movement": "keluar|masuk",  // keluar=penjualan (NEGATIVE), masuk=pembelian (POSITIVE)
    "lokasi_gudang": "",
    "items_inventory": [{{"produk_id": "", "jumlah_movement": -10.0, "stok_setelah": 0}}]
  }},
  "kategori_arus_kas": "operasi|investasi|pendanaan"
}}
NOTE: For incomplete transactions (e.g. "jual kopi", "beli kain"), return transaction_record with minimal entities:
- jenis_transaksi: REQUIRED (detect from keyword: "jual"→penjualan, "beli"→pembelian, "bayar"→beban)
- items: [] or [{{"nama_produk": "kopi"}}] if product name mentioned
- total_nominal: null if missing
- metode_pembayaran: null if missing
- Other fields: null if missing (will be asked via multi-turn conversation)

retur_penjualan:
{{
  "jenis_transaksi": "retur_penjualan",
  "total_nominal": -150000,
  "keterangan": "Retur: rusak|salah kirim|tidak sesuai",
  "metadata": {{"is_retur": true, "alasan_retur": "rusak"}},
  "items": [{{"nama_produk": "x", "jumlah": 3, "satuan": "pcs", "harga_satuan": 50000, "subtotal": 150000}}],
  "inventory_impact": {{
    "is_tracked": true,
    "jenis_movement": "masuk",
    "lokasi_gudang": "",
    "items_inventory": [{{"produk_id": "", "jumlah_movement": 3.0, "stok_setelah": 0}}]
  }}
}}

retur_pembelian:
{{
  "is_retur": true,
  "jenis_transaksi": "retur_pembelian",
  "total_nominal": -200000,  // NEGATIVE (money back from supplier)
  "nama_pihak": "supplier_name",
  "items": [{{"nama_produk": "x", "jumlah": 5, "satuan": "pcs", "harga_satuan": 40000}}]
}}

pembayaran_hutang:
{{
  "jenis_transaksi": "pembayaran_hutang",
  "total_nominal": 5000000,  // Amount being paid NOW
  "nama_pihak": "supplier_name",
  "total_hutang_awal": 20000000,  // Original debt
  "sisa_hutang": 15000000,  // Remaining after payment
  "metode_pembayaran": "cash|transfer"
}}

financial_report:
{{"report_type": "laba_rugi|neraca|arus_kas", "periode_pelaporan": "YYYY-MM"}}
NOTE: Salary payment queries (e.g. "sudah bayar gaji siapa saja", "gaji siapa yang belum dibayar", "gaji bulan November") should be classified as financial_report, NOT transaction_record

top_products:
{{"time_range": "daily|weekly|monthly|yearly", "limit": 10}}

low_sell_products:
{{"time_range": "monthly", "limit": 10, "threshold": 0}}

query_transaksi:
{{"supplier_name": "Toko Kain Jaya", "customer_name": "", "date_range": "2024-10", "jenis_transaksi": "pembelian|penjualan"}}

inventory_query:
{{"query_type": "stock_level", "product_name": "ballpoint", "lokasi_gudang": ""}}

inventory_history:
{{"query_type": "movement_history", "product_name": "ballpoint", "lokasi_gudang": "", "date_range": "2024-10"}}

koreksi:
{{
  "field_to_update": "total_nominal|metode_pembayaran|items|keterangan",
  "new_value": "value atau object",
  "reference": "transaksi_tadi|transaksi_terakhir|yang_tadi"
}}

CRITICAL RULES:
- Amounts in RUPIAH (integer, NO cents conversion)
- Rp 5.000 → 5000 | Rp 5.000.000 → 5000000
- DO NOT multiply by 100
- "@Rp 75.000" means harga_satuan (price PER UNIT), NOT total
- total_nominal MUST = SUM of all (jumlah × harga_satuan)
- For multi-item: "10 kemeja @150rb, 5 celana @200rb" → total_nominal = (10×150000) + (5×200000) = 2500000
- ALWAYS calculate subtotal per item: subtotal = jumlah × harga_satuan
- **MANDATORY FOR transaction_record**: ALWAYS extract "items" array, even for single item transactions
- **MANDATORY**: "jual 10 kopi @20000" MUST extract items=[{{nama_produk:"kopi",jumlah:10,harga_satuan:20000,subtotal:200000}}]
- **MANDATORY**: Never skip items extraction. If quantity & product exist, create items array
- Penjualan: jenis_movement="keluar", jumlah_movement NEGATIVE
- Pembelian: jenis_movement="masuk", jumlah_movement POSITIVE
- RETUR: Use negative total_nominal, opposite jenis_movement
- KEYWORDS: "return", "retur", "rusak", "refund" → retur_penjualan
- KEYWORDS: "bayar cicilan", "bayar hutang", "pelunasan" → pembayaran_hutang
- ALWAYS extract product_name for inventory_query
- Output: pure JSON, no markdown

Examples:

Q: "jual 50 buku @10rb"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"penjualan","total_nominal":500000,"items":[{{"nama_produk":"buku","jumlah":50,"satuan":"pcs","harga_satuan":10000,"subtotal":500000}}],"inventory_impact":{{"is_tracked":true,"jenis_movement":"keluar","items_inventory":[{{"produk_id":"","jumlah_movement":-50.0}}]}}}},"confidence":0.95}}

Q: "jual kopi"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"penjualan","items":[{{"nama_produk":"kopi"}}],"total_nominal":null,"metode_pembayaran":null}},"confidence":0.90}}

Q: "beli kain"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"pembelian","items":[{{"nama_produk":"kain"}}],"total_nominal":null,"metode_pembayaran":null}},"confidence":0.90}}

Q: "jual jasa konsultasi"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"penjualan","items":[{{"nama_produk":"jasa konsultasi"}}],"total_nominal":null,"metode_pembayaran":null}},"confidence":0.90}}

Q: "catat penjualan: 10 kemeja @150rb, 5 celana @200rb, 3 jaket @350rb"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"penjualan","total_nominal":3550000,"items":[{{"nama_produk":"kemeja","jumlah":10,"harga_satuan":150000,"subtotal":1500000}},{{"nama_produk":"celana","jumlah":5,"harga_satuan":200000,"subtotal":1000000}},{{"nama_produk":"jaket","jumlah":3,"harga_satuan":350000,"subtotal":1050000}}]}},"confidence":0.95}}

Q: "customer return 3 pcs kemeja rusak, refund Rp450rb"
A: {{"intent":"retur_penjualan","entities":{{"is_retur":true,"jenis_transaksi":"retur_penjualan","total_nominal":-450000,"alasan_retur":"rusak","items":[{{"nama_produk":"kemeja","jumlah":3,"harga_satuan":150000}}],"inventory_impact":{{"is_tracked":true,"jenis_movement":"masuk","items_inventory":[{{"produk_id":"","jumlah_movement":3.0}}]}}}},"confidence":0.95}}

Q: "bayar cicilan utang ke supplier Rp5jt dari total Rp20jt"
A: {{"intent":"pembayaran_hutang","entities":{{"jenis_transaksi":"pembayaran_hutang","total_nominal":5000000,"total_hutang_awal":20000000,"sisa_hutang":15000000,"metode_pembayaran":"cash"}},"confidence":0.95}}

Q: "bayar gaji 5 karyawan masing-masing 3jt, listrik 2jt, sewa 10jt"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"beban","total_nominal":27000000,"kategori_beban":"beban_gaji","items":[{{"nama_produk":"gaji karyawan","jumlah":5,"harga_satuan":3000000,"subtotal":15000000}},{{"nama_produk":"listrik","jumlah":1,"harga_satuan":2000000,"subtotal":2000000,"kategori_beban":"beban_listrik"}},{{"nama_produk":"sewa","jumlah":1,"harga_satuan":10000000,"subtotal":10000000,"kategori_beban":"beban_sewa"}}]}},"confidence":0.95}}

Q: "bayar gaji Rp 50 juta untuk karyawan"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"beban","total_nominal":50000000,"kategori_beban":"beban_gaji"}},"confidence":0.95}}

Q: "Bayar gaji bulan November: Anna Rp 3juta, Bambang Rp 3 juta, Inggrid Rp 3 JUTA"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"beban","total_nominal":9000000,"kategori_beban":"beban_gaji","detail_karyawan":"Anna, Bambang, Inggrid","periode_gaji":"November"}},"confidence":0.95}}

Q: "bayar gaji untuk Anna, Bambang, dan Inggrid masing-masing 3 juta, bulan November"
A: {{"intent":"transaction_record","entities":{{"jenis_transaksi":"beban","total_nominal":9000000,"kategori_beban":"beban_gaji","detail_karyawan":"Anna, Bambang, Inggrid","periode_gaji":"November"}},"confidence":0.95}}

Q: "tampilkan transaksi supplier Toko Kain Jaya bulan Oktober"
A: {{"intent":"query_transaksi","entities":{{"supplier_name":"Toko Kain Jaya","date_range":"2024-10","jenis_transaksi":"pembelian"}},"confidence":0.95}}

Q: "produk mana yang kurang laku?"
A: {{"intent":"low_sell_products","entities":{{"time_range":"monthly","limit":10}},"confidence":0.95}}

Q: "stok pensil berapa?"
A: {{"intent":"inventory_query","entities":{{"query_type":"stock_level","product_name":"pensil","lokasi_gudang":""}},"confidence":0.95}}

Q: "riwayat stok kain cotton bulan lalu"
A: {{"intent":"inventory_history","entities":{{"query_type":"movement_history","product_name":"kain cotton","date_range":"2024-10"}},"confidence":0.95}}

Q: "history pergerakan stok ballpoint"
A: {{"intent":"inventory_history","entities":{{"query_type":"movement_history","product_name":"ballpoint"}},"confidence":0.95}}

Q: "ada koreksi, nominalnya salah harusnya Rp500rb"
A: {{"intent":"koreksi","entities":{{"field_to_update":"total_nominal","new_value":500000,"reference":"transaksi_tadi"}},"confidence":0.95}}

Q: "ubah transaksi tadi jadi Rp1jt"
A: {{"intent":"koreksi","entities":{{"field_to_update":"total_nominal","new_value":1000000,"reference":"transaksi_tadi"}},"confidence":0.95}}

Q: "sudah bayar gaji siapa saja bulan ini?"
A: {{"intent":"financial_report","entities":{{"report_type":"laba_rugi","periode_pelaporan":"2025-01"}},"confidence":0.95}}

Q: "gaji bulan November sudah bayar siapa saja?"
A: {{"intent":"financial_report","entities":{{"report_type":"laba_rugi","periode_pelaporan":"2024-11"}},"confidence":0.95}}

Q: "berapa total pengeluaran untuk gaji di bulan Desember?"
A: {{"intent":"financial_report","entities":{{"report_type":"laba_rugi","periode_pelaporan":"2024-12"}},"confidence":0.95}}

USER: {user_input}
TENANT: {tenant_id}
TODAY: {today}
PERIOD: {current_period}

JSON:"""
