"""
500-Query Multi-Turn Discovery Test
12 categories × ~42 queries each = 500+ queries
Goal: Find architecture gaps, not confirm happy paths.

DIAGNOSTIC: log everything, fix nothing.
"""
import httpx, json, uuid, time, asyncio

BASE = "http://localhost:8001"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"

# Format: (group_name, category, [(query, must_contain_any, must_not_contain, max_latency_ms)])

GROUPS = [
    # ══════════════════════════════════════════════════════════════
    # CATEGORY 1: SELF-REFERENTIAL FOLLOW-UP (50 queries, 10 groups)
    # User refers to data bot JUST gave
    # ══════════════════════════════════════════════════════════════

    ("G_SR01_items_drill", "self_referential", [
        ("ada stok apa aja?", ["stok", "barang", "item", "produk"], [], 8000),
        ("yang pertama harganya berapa?", ["harga", "Rp"], [], 8000),
        ("stoknya di gudang mana?", ["gudang", "warehouse", "stok"], [], 8000),
        ("update harganya jadi 200rb", ["200", "harga", "ubah", "update", "konfirmasi"], [], 10000),
        ("batal deh", ["batal", "cancel", "oke", "dibatalkan"], [], 5000),
    ]),

    ("G_SR02_customer_drill", "self_referential", [
        ("daftar pelanggan", ["pelanggan", "customer"], [], 8000),
        ("yang terakhir piutangnya berapa?", ["piutang", "Rp"], [], 8000),
        ("detail lengkapnya?", ["detail", "pelanggan", "telepon", "alamat", "email"], [], 8000),
        ("ada transaksi apa aja?", ["transaksi", "faktur", "invoice"], [], 8000),
        ("yang belum lunas?", ["belum", "lunas", "Rp", "outstanding"], [], 8000),
    ]),

    ("G_SR03_ap_vendor_drill", "self_referential", [
        ("hutang ke siapa aja?", ["hutang", "vendor", "Rp"], [], 8000),
        ("yang pertama fakturnya apa aja?", ["faktur", "BILL", "tagihan"], [], 8000),
        ("bayar yang terkecil", ["bayar", "Rp", "konfirmasi", "tagihan"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("totalnya berapa?", ["total", "Rp"], [], 8000),
    ]),

    ("G_SR04_expense_drill", "self_referential", [
        ("pengeluaran bulan ini", ["pengeluaran", "biaya", "Rp"], [], 8000),
        ("yang terbesar detailnya?", ["detail", "Rp", "akun", "biaya"], [], 8000),
        ("akun apa itu?", ["akun", "beban", "account"], [], 8000),
        ("ada biaya serupa bulan lalu?", ["biaya", "bulan", "lalu", "serupa"], [], 15000),
        ("bandingkan totalnya", ["total", "bandingkan", "Rp", "bulan"], [], 15000),
    ]),

    ("G_SR05_invoice_drill", "self_referential", [
        ("faktur penjualan bulan ini", ["faktur", "penjualan", "INV"], [], 8000),
        ("yang pertama detailnya?", ["detail", "faktur", "INV", "Rp"], [], 8000),
        ("sudah dibayar belum?", ["bayar", "lunas", "Rp", "belum", "sudah"], [], 8000),
        ("siapa pelanggannya?", ["pelanggan", "customer"], [], 8000),
        ("piutang dia berapa?", ["piutang", "Rp"], [], 8000),
    ]),

    ("G_SR06_bank_drill", "self_referential", [
        ("daftar rekening", ["rekening", "bank", "kas"], [], 8000),
        ("yang saldonya paling besar?", ["saldo", "Rp", "terbesar"], [], 8000),
        ("transaksi terakhir di rekening itu?", ["transaksi", "Rp"], [], 8000),
        ("itu masuk atau keluar?", ["masuk", "keluar", "debit", "kredit"], [], 8000),
        ("dari siapa uangnya?", ["dari", "vendor", "pelanggan", "customer"], [], 8000),
    ]),

    ("G_SR07_vendor_drill", "self_referential", [
        ("daftar vendor", ["vendor", "pemasok"], [], 8000),
        ("yang pertama hutang berapa?", ["hutang", "Rp", "AP"], [], 8000),
        ("detail lengkapnya?", ["detail", "vendor", "telepon", "alamat"], [], 8000),
        ("faktur pembelian dari mereka?", ["faktur", "pembelian", "BILL"], [], 8000),
        ("totalnya berapa?", ["total", "Rp"], [], 8000),
    ]),

    ("G_SR08_items_category", "self_referential", [
        ("daftar kategori barang", ["kategori", "category"], [], 8000),
        ("yang pertama ada barang apa aja?", ["barang", "item", "produk"], [], 8000),
        ("mana yang paling mahal?", ["mahal", "harga", "Rp"], [], 8000),
        ("stoknya berapa?", ["stok", "pcs", "qty"], [], 8000),
        ("restock perlu ga?", ["stok", "perlu", "restock", "rendah", "habis", "aman"], [], 15000),
    ]),

    ("G_SR09_overdue_drill", "self_referential", [
        ("ada faktur yang overdue?", ["overdue", "jatuh tempo", "faktur"], [], 8000),
        ("yang paling lama berapa hari?", ["hari", "overdue", "jatuh tempo", "lama"], [], 8000),
        ("dari pelanggan siapa?", ["pelanggan", "customer"], [], 8000),
        ("contact dia apa?", ["telepon", "email", "contact", "pelanggan"], [], 8000),
        ("mau ditagih", ["tagih", "bayar", "kirim", "reminder"], [], 10000),
    ]),

    ("G_SR10_bills_drill", "self_referential", [
        ("faktur pembelian yang belum lunas", ["faktur", "pembelian", "belum", "lunas", "BILL"], [], 8000),
        ("yang paling besar dari vendor siapa?", ["vendor", "Rp", "terbesar"], [], 8000),
        ("jatuh temponya kapan?", ["jatuh tempo", "tanggal", "due"], [], 8000),
        ("sisa yang harus dibayar?", ["sisa", "Rp", "bayar", "outstanding"], [], 8000),
        ("bayar dari BCA", ["BCA", "bayar", "konfirmasi", "rekening"], [], 10000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 2: ENTITY CARRY-FORWARD (50 queries, 10 groups)
    # Entity from T1 must persist without re-mentioning
    # ══════════════════════════════════════════════════════════════

    ("G_EC01_customer_ar", "entity_carry", [
        ("piutang Sintia berapa?", ["Sintia", "piutang", "Rp"], [], 8000),
        ("fakturnya apa aja?", ["faktur", "INV", "Sintia"], [], 8000),
        ("yang paling besar?", ["Rp", "terbesar", "faktur"], [], 8000),
        ("sudah jatuh tempo?", ["jatuh tempo", "overdue", "belum", "tanggal"], [], 8000),
        ("mau ditagih", ["tagih", "kirim", "Sintia", "konfirmasi"], [], 10000),
    ]),

    ("G_EC02_vendor_ap", "entity_carry", [
        ("detail vendor Knitto", ["Knitto", "vendor", "detail"], [], 8000),
        ("hutang ke mereka berapa?", ["hutang", "Rp", "Knitto"], [], 8000),
        ("faktur pembeliannya?", ["faktur", "BILL", "pembelian"], [], 8000),
        ("bayar yang terkecil", ["bayar", "Rp", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_EC03_item_detail", "entity_carry", [
        ("stok poloshirt hitam berapa?", ["poloshirt", "stok", "pcs"], [], 8000),
        ("harga jualnya?", ["harga", "jual", "Rp"], [], 8000),
        ("harga belinya?", ["harga", "beli", "Rp"], [], 8000),
        ("marginnya berapa?", ["margin", "Rp", "selisih", "untung"], [], 15000),
        ("update harga jual jadi 200rb", ["200", "harga", "ubah", "konfirmasi"], [], 10000),
    ]),

    ("G_EC04_bank_tx", "entity_carry", [
        ("saldo BCA berapa?", ["BCA", "saldo", "Rp"], [], 8000),
        ("transaksi bulan ini di situ?", ["transaksi", "BCA", "Rp"], [], 8000),
        ("yang terbesar?", ["terbesar", "Rp", "transaksi"], [], 8000),
        ("itu untuk apa?", ["detail", "keterangan", "akun"], [], 8000),
        ("ada di akun beban mana?", ["akun", "beban", "account"], [], 8000),
    ]),

    ("G_EC05_invoice_create", "entity_carry", [
        ("buat faktur untuk Sintia", ["Sintia", "faktur", "penjualan", "konfirmasi"], [], 10000),
        ("barangnya poloshirt 20 pcs", ["poloshirt", "20", "pcs"], [], 8000),
        ("harga 150rb", ["150", "harga", "Rp"], [], 8000),
        ("tanggal hari ini", ["tanggal", "hari ini", "konfirmasi", "review"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_EC06_expense_create", "entity_carry", [
        ("catat biaya listrik 450rb", ["listrik", "450", "biaya", "konfirmasi"], [], 10000),
        ("dari kas BCA", ["BCA", "kas", "rekening"], [], 8000),
        ("akun beban utilitas", ["utilitas", "akun", "beban"], [], 8000),
        ("tanggal hari ini", ["tanggal", "konfirmasi", "review"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_EC07_vendor_deep", "entity_carry", [
        ("info vendor PT Top Tri Land", ["Top Tri", "vendor", "detail"], [], 8000),
        ("dia jual apa ke kita?", ["barang", "faktur", "pembelian", "jual"], [], 8000),
        ("hutangnya?", ["hutang", "Rp", "AP"], [], 8000),
        ("bayar 500rb", ["500", "bayar", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_EC08_customer_deep", "entity_carry", [
        ("info pelanggan PT Maju Jaya", ["Maju Jaya", "pelanggan", "detail"], [], 8000),
        ("piutangnya berapa?", ["piutang", "Rp"], [], 8000),
        ("faktur belum lunas?", ["faktur", "belum", "lunas", "INV"], [], 8000),
        ("kirim reminder", ["kirim", "reminder", "tagih", "konfirmasi"], [], 10000),
        ("detailnya?", ["detail", "faktur", "Rp"], [], 8000),
    ]),

    ("G_EC09_bill_vendor", "entity_carry", [
        ("faktur pembelian dari Knitto", ["Knitto", "faktur", "pembelian", "BILL"], [], 8000),
        ("yang belum lunas?", ["belum", "lunas", "Rp", "outstanding"], [], 8000),
        ("totalnya?", ["total", "Rp"], [], 8000),
        ("bayar semua dari BCA", ["bayar", "BCA", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_EC10_item_stock", "entity_carry", [
        ("cek barang Lacoste Pique", ["Lacoste", "barang", "item"], [], 8000),
        ("stoknya?", ["stok", "pcs", "qty"], [], 8000),
        ("di gudang mana?", ["gudang", "warehouse"], [], 8000),
        ("harga beli dari vendor?", ["harga", "beli", "Rp", "vendor"], [], 8000),
        ("perlu restock?", ["stok", "restock", "perlu", "rendah", "aman"], [], 15000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 3: CORRECTION + AMENDMENT (40 queries, 8 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_CA01_qty_correct", "correction", [
        ("buat faktur Sintia", ["Sintia", "faktur", "penjualan", "konfirmasi"], [], 10000),
        ("poloshirt 20 pcs", ["poloshirt", "20"], [], 8000),
        ("eh salah, 30 pcs", ["30", "pcs", "ubah", "ganti"], [], 8000),
        ("harga 150rb", ["150", "harga"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA02_amount_correct", "correction", [
        ("bayar tagihan Knitto", ["Knitto", "bayar", "tagihan", "konfirmasi"], [], 10000),
        ("500 ribu", ["500", "Rp"], [], 8000),
        ("eh salah, 800 ribu", ["800", "Rp", "ubah", "ganti"], [], 8000),
        ("dari BCA", ["BCA", "rekening"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA03_type_correct", "correction", [
        ("tambah vendor PT ABC", ["ABC", "vendor", "konfirmasi"], [], 10000),
        ("barang", ["barang", "produk", "jenis"], [], 8000),
        ("eh jasa maksudnya", ["jasa", "service", "ubah", "ganti"], [], 8000),
        ("alamat Jl Merdeka 10", ["Merdeka", "alamat"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA04_account_correct", "correction", [
        ("buat biaya listrik 500rb", ["listrik", "500", "biaya", "konfirmasi"], [], 10000),
        ("akun beban utilitas", ["utilitas", "akun"], [], 8000),
        ("eh bukan, akun beban operasional", ["operasional", "akun", "ubah", "ganti"], [], 8000),
        ("dari kas kecil", ["kas", "kecil", "rekening"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA05_customer_correct", "correction", [
        ("buat faktur untuk Budi", ["Budi", "faktur", "konfirmasi"], [], 10000),
        ("eh bukan Budi, maksudnya Sintia", ["Sintia", "faktur", "ganti", "ubah"], [], 10000),
        ("poloshirt 10 pcs 155rb", ["poloshirt", "10", "155"], [], 8000),
        ("ralat, 12 pcs", ["12", "pcs", "ubah", "ganti", "ralat"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA06_bank_correct", "correction", [
        ("bayar hutang ke Knitto 100rb", ["Knitto", "100", "bayar", "konfirmasi"], [], 10000),
        ("dari BCA", ["BCA", "rekening"], [], 8000),
        ("eh dari kas kecil aja", ["kas", "kecil", "ubah", "ganti"], [], 8000),
        ("tanggal hari ini", ["tanggal", "hari ini"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA07_date_correct", "correction", [
        ("buat faktur Sintia tanggal 15", ["Sintia", "faktur", "15", "konfirmasi"], [], 10000),
        ("poloshirt 5 pcs 150rb", ["poloshirt", "5", "150"], [], 8000),
        ("eh tanggalnya 20 dong", ["20", "tanggal", "ubah", "ganti"], [], 8000),
        ("jatuh tempo 30 hari", ["30", "jatuh tempo"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_CA08_item_correct", "correction", [
        ("buat faktur Sintia", ["Sintia", "faktur", "konfirmasi"], [], 10000),
        ("barangnya Lacoste 10 pcs", ["Lacoste", "10"], [], 8000),
        ("eh bukan Lacoste, poloshirt", ["poloshirt", "ubah", "ganti"], [], 8000),
        ("harga 155rb", ["155", "harga"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 4: CROSS-MODULE REFERENCE (40 queries, 8 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_CM01_ar_to_items", "cross_module", [
        ("piutang Sintia berapa?", ["Sintia", "piutang", "Rp"], [], 8000),
        ("barang apa yang dia beli?", ["barang", "item", "Sintia", "faktur"], [], 15000),
        ("stok barang itu masih ada?", ["stok", "pcs", "ada"], [], 8000),
        ("harga jualnya berapa?", ["harga", "jual", "Rp"], [], 8000),
        ("marginnya?", ["margin", "selisih", "Rp", "untung"], [], 15000),
    ]),

    ("G_CM02_ap_to_items", "cross_module", [
        ("hutang ke Knitto berapa?", ["Knitto", "hutang", "Rp"], [], 8000),
        ("mereka jual barang apa ke kita?", ["barang", "item", "Knitto", "beli"], [], 15000),
        ("stoknya masih ada?", ["stok", "pcs", "ada"], [], 8000),
        ("biaya pengiriman dari mereka?", ["pengiriman", "biaya", "Rp", "Knitto"], [], 15000),
        ("total cost?", ["total", "cost", "Rp", "biaya"], [], 15000),
    ]),

    ("G_CM03_bank_to_vendor", "cross_module", [
        ("saldo BCA berapa?", ["BCA", "saldo", "Rp"], [], 8000),
        ("transaksi terakhir apa?", ["transaksi", "Rp", "terakhir"], [], 8000),
        ("itu pembayaran ke vendor siapa?", ["vendor", "pembayaran"], [], 15000),
        ("kita masih hutang berapa ke mereka?", ["hutang", "Rp", "vendor"], [], 8000),
        ("fakturnya?", ["faktur", "BILL", "tagihan"], [], 8000),
    ]),

    ("G_CM04_pl_to_customer", "cross_module", [
        ("laba rugi bulan ini", ["laba", "rugi", "pendapatan", "beban", "Rp"], [], 15000),
        ("pendapatan terbesar dari mana?", ["pendapatan", "terbesar", "Rp"], [], 15000),
        ("dari pelanggan siapa?", ["pelanggan", "customer"], [], 15000),
        ("piutang dia berapa?", ["piutang", "Rp"], [], 8000),
        ("barang apa yang dia beli?", ["barang", "item", "beli"], [], 15000),
    ]),

    ("G_CM05_items_to_vendor", "cross_module", [
        ("stok poloshirt hitam", ["poloshirt", "stok", "pcs"], [], 8000),
        ("beli dari vendor mana?", ["vendor", "beli", "pemasok"], [], 15000),
        ("harga beli terakhir?", ["harga", "beli", "Rp"], [], 8000),
        ("hutang ke vendor itu berapa?", ["hutang", "Rp", "vendor"], [], 8000),
        ("tagihan terbaru?", ["tagihan", "faktur", "BILL"], [], 8000),
    ]),

    ("G_CM06_expense_to_bank", "cross_module", [
        ("pengeluaran terbesar bulan ini?", ["pengeluaran", "biaya", "Rp", "terbesar"], [], 8000),
        ("dari rekening mana?", ["rekening", "bank", "kas"], [], 8000),
        ("sisa saldo rekening itu?", ["saldo", "Rp", "rekening"], [], 8000),
        ("cukup buat bayar hutang?", ["hutang", "cukup", "Rp", "saldo"], [], 15000),
        ("berapa kekurangannya?", ["kekurangan", "selisih", "Rp", "kurang"], [], 15000),
    ]),

    ("G_CM07_customer_to_invoice", "cross_module", [
        ("pelanggan aktif siapa aja?", ["pelanggan", "aktif", "customer"], [], 8000),
        ("Sintia beli apa bulan ini?", ["Sintia", "beli", "faktur", "barang"], [], 15000),
        ("total pembeliannya?", ["total", "Rp", "pembelian", "penjualan"], [], 8000),
        ("sudah bayar semua?", ["bayar", "lunas", "piutang", "Rp"], [], 8000),
        ("sisa yang belum?", ["sisa", "belum", "Rp", "outstanding"], [], 8000),
    ]),

    ("G_CM08_vendor_to_expense", "cross_module", [
        ("vendor Noneng jual apa?", ["Noneng", "vendor", "barang", "jual"], [], 15000),
        ("total pembelian ke Noneng?", ["total", "pembelian", "Rp", "Noneng"], [], 8000),
        ("ada biaya lain dari Noneng?", ["biaya", "Noneng", "Rp"], [], 15000),
        ("hutang ke Noneng?", ["hutang", "Rp", "Noneng"], [], 8000),
        ("bayar dari BCA bisa?", ["bayar", "BCA", "konfirmasi"], [], 10000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 5: NUMERICAL REASONING (40 queries, 8 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_NR01_net_position", "numerical", [
        ("piutang berapa?", ["piutang", "Rp"], [], 8000),
        ("hutang berapa?", ["hutang", "Rp"], [], 8000),
        ("mana yang lebih besar?", ["lebih besar", "piutang", "hutang"], [], 15000),
        ("selisihnya berapa?", ["selisih", "Rp", "beda"], [], 15000),
        ("net position kita gimana?", ["net", "posisi", "Rp", "piutang", "hutang"], [], 15000),
    ]),

    ("G_NR02_cash_sufficiency", "numerical", [
        ("saldo semua bank?", ["saldo", "Rp", "bank", "rekening"], [], 8000),
        ("total hutang kita?", ["hutang", "Rp", "total"], [], 8000),
        ("cukup ga buat bayar semua hutang?", ["cukup", "hutang", "saldo", "Rp"], [], 15000),
        ("sisa berapa kalau hutang dibayar semua?", ["sisa", "Rp", "bayar"], [], 15000),
        ("aman ga posisi kas kita?", ["aman", "kas", "posisi", "Rp"], [], 15000),
    ]),

    ("G_NR03_margin_calc", "numerical", [
        ("harga jual poloshirt?", ["harga", "jual", "Rp", "poloshirt"], [], 8000),
        ("harga belinya?", ["harga", "beli", "Rp"], [], 8000),
        ("margin per unit berapa?", ["margin", "Rp", "untung", "selisih"], [], 15000),
        ("kalau jual 100 pcs untung berapa?", ["100", "untung", "Rp", "margin"], [], 15000),
        ("persentase marginnya?", ["persen", "margin", "%"], [], 15000),
    ]),

    ("G_NR04_month_comparison", "numerical", [
        ("penjualan bulan ini berapa?", ["penjualan", "Rp"], [], 8000),
        ("pengeluaran bulan ini?", ["pengeluaran", "Rp"], [], 8000),
        ("untung atau rugi?", ["untung", "rugi", "laba", "Rp"], [], 15000),
        ("marginnya berapa persen?", ["margin", "persen", "%", "Rp"], [], 15000),
        ("bulan lalu lebih baik atau lebih buruk?", ["bulan lalu", "lebih", "perbandingan", "Rp"], [], 15000),
    ]),

    ("G_NR05_ar_analysis", "numerical", [
        ("total piutang berapa?", ["piutang", "Rp", "total"], [], 8000),
        ("berapa yang overdue?", ["overdue", "jatuh tempo", "Rp"], [], 8000),
        ("persentase overdue dari total?", ["persen", "overdue", "%", "Rp"], [], 15000),
        ("rata-rata hari overdue?", ["rata-rata", "hari", "overdue"], [], 15000),
        ("pelanggan paling banyak nunggak?", ["pelanggan", "nunggak", "overdue", "Rp"], [], 8000),
    ]),

    ("G_NR06_expense_ratio", "numerical", [
        ("total pendapatan bulan ini?", ["pendapatan", "penjualan", "Rp"], [], 8000),
        ("total biaya bulan ini?", ["biaya", "pengeluaran", "Rp"], [], 8000),
        ("expense ratio berapa?", ["ratio", "persen", "%", "expense", "biaya"], [], 15000),
        ("terlalu tinggi ga?", ["tinggi", "normal", "sehat", "persen"], [], 15000),
        ("harus kurangi biaya apa?", ["kurangi", "biaya", "akun", "Rp", "saran"], [], 15000),
    ]),

    ("G_NR07_stock_value", "numerical", [
        ("total nilai stok semua barang?", ["stok", "nilai", "Rp", "total"], [], 8000),
        ("berapa persen dari total aset?", ["persen", "aset", "%", "stok"], [], 15000),
        ("barang mana yang paling besar nilainya?", ["barang", "terbesar", "nilai", "Rp"], [], 8000),
        ("turnover-nya gimana?", ["turnover", "perputaran", "stok"], [], 15000),
        ("perlu kurangi stok barang apa?", ["kurangi", "stok", "barang", "saran"], [], 15000),
    ]),

    ("G_NR08_ap_ar_ratio", "numerical", [
        ("total piutang?", ["piutang", "Rp", "total"], [], 8000),
        ("total hutang?", ["hutang", "Rp", "total"], [], 8000),
        ("current ratio kita berapa?", ["ratio", "current", "rasio"], [], 15000),
        ("posisi keuangan sehat?", ["sehat", "keuangan", "posisi", "baik"], [], 15000),
        ("rekomendasi apa?", ["rekomendasi", "saran", "prioritas"], [], 15000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 6: TEMPORAL QUERIES (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_TQ01_sales_trend", "temporal", [
        ("penjualan hari ini?", ["penjualan", "Rp", "hari ini"], [], 8000),
        ("minggu ini totalnya?", ["minggu", "total", "Rp", "penjualan"], [], 8000),
        ("bulan ini?", ["bulan", "penjualan", "Rp"], [], 8000),
        ("bulan lalu berapa?", ["bulan lalu", "Rp", "penjualan"], [], 15000),
        ("trendnya naik atau turun?", ["trend", "naik", "turun", "Rp", "perbandingan"], [], 15000),
    ]),

    ("G_TQ02_overdue_timeline", "temporal", [
        ("tagihan jatuh tempo minggu ini?", ["jatuh tempo", "minggu", "tagihan", "faktur"], [], 8000),
        ("yang sudah lewat?", ["lewat", "overdue", "jatuh tempo"], [], 8000),
        ("berapa hari rata-rata telat?", ["rata-rata", "hari", "telat", "overdue"], [], 15000),
        ("total overdue?", ["total", "overdue", "Rp"], [], 8000),
        ("yang paling lama?", ["paling lama", "overdue", "hari"], [], 8000),
    ]),

    ("G_TQ03_expense_compare", "temporal", [
        ("biaya bulan ini berapa?", ["biaya", "Rp", "bulan ini"], [], 8000),
        ("bulan lalu?", ["bulan lalu", "Rp", "biaya"], [], 15000),
        ("naik atau turun?", ["naik", "turun", "biaya", "perbandingan"], [], 15000),
        ("kategori mana yang naik?", ["kategori", "naik", "akun", "biaya"], [], 15000),
        ("rinciannya?", ["rincian", "detail", "akun", "biaya", "Rp"], [], 8000),
    ]),

    ("G_TQ04_payment_timeline", "temporal", [
        ("pembayaran masuk bulan ini?", ["pembayaran", "masuk", "Rp", "bulan"], [], 8000),
        ("dari pelanggan siapa aja?", ["pelanggan", "customer", "pembayaran"], [], 8000),
        ("minggu lalu ada masuk ga?", ["minggu lalu", "pembayaran", "masuk", "Rp"], [], 15000),
        ("rata-rata per minggu berapa?", ["rata-rata", "minggu", "Rp", "pembayaran"], [], 15000),
        ("prediksi bulan depan?", ["prediksi", "bulan depan", "estimasi", "Rp"], [], 15000),
    ]),

    ("G_TQ05_cashflow_period", "temporal", [
        ("arus kas bulan ini?", ["arus kas", "cash flow", "Rp"], [], 15000),
        ("bulan lalu gimana?", ["bulan lalu", "arus kas", "Rp"], [], 15000),
        ("lebih baik atau buruk?", ["lebih", "baik", "buruk", "perbandingan"], [], 15000),
        ("penyebab utama?", ["penyebab", "utama", "karena", "akun"], [], 15000),
        ("posisi kas aman?", ["posisi", "kas", "aman", "Rp", "sehat"], [], 15000),
    ]),

    ("G_TQ06_invoice_aging", "temporal", [
        ("aging piutang?", ["aging", "piutang", "AR", "Rp"], [], 8000),
        ("yang > 30 hari berapa?", ["30", "hari", "Rp", "overdue"], [], 8000),
        ("yang > 60 hari?", ["60", "hari", "Rp", "overdue"], [], 8000),
        ("siapa pelanggan terburuk?", ["pelanggan", "terburuk", "overdue", "terlama"], [], 8000),
        ("tindakan apa yang harus diambil?", ["tindakan", "saran", "tagih", "prioritas"], [], 15000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 7: CONDITIONAL/FILTER QUERIES (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_CF01_item_filter", "filter", [
        ("daftar barang", ["barang", "item", "produk"], [], 8000),
        ("yang harganya di atas 100rb", ["harga", "Rp", "100", "barang"], [], 8000),
        ("dari itu yang stoknya lebih dari 10", ["stok", "10", "barang"], [], 8000),
        ("urutkan dari termahal", ["urut", "mahal", "Rp", "harga"], [], 8000),
        ("top 3 aja", ["top", "3", "barang", "Rp"], [], 8000),
    ]),

    ("G_CF02_invoice_filter", "filter", [
        ("faktur penjualan", ["faktur", "penjualan", "INV"], [], 8000),
        ("bulan ini aja", ["bulan ini", "faktur", "Rp"], [], 8000),
        ("yang belum lunas", ["belum", "lunas", "faktur", "Rp"], [], 8000),
        ("untuk pelanggan Sintia", ["Sintia", "faktur", "Rp"], [], 8000),
        ("totalnya berapa?", ["total", "Rp"], [], 8000),
    ]),

    ("G_CF03_expense_filter", "filter", [
        ("daftar pengeluaran", ["pengeluaran", "biaya", "Rp"], [], 8000),
        ("yang lebih dari 1 juta", ["1", "juta", "Rp", "biaya"], [], 8000),
        ("bulan ini aja", ["bulan ini", "biaya", "Rp"], [], 8000),
        ("kategori operasional", ["operasional", "akun", "biaya"], [], 8000),
        ("rincian per akun", ["akun", "rincian", "Rp", "biaya"], [], 8000),
    ]),

    ("G_CF04_vendor_filter", "filter", [
        ("daftar vendor", ["vendor", "pemasok"], [], 8000),
        ("yang kita punya hutang", ["hutang", "vendor", "Rp"], [], 8000),
        ("hutang di atas 500rb", ["500", "hutang", "Rp", "vendor"], [], 8000),
        ("yang overdue", ["overdue", "jatuh tempo", "vendor"], [], 8000),
        ("urutkan dari terbesar", ["urut", "terbesar", "Rp", "hutang"], [], 8000),
    ]),

    ("G_CF05_customer_filter", "filter", [
        ("daftar pelanggan", ["pelanggan", "customer"], [], 8000),
        ("yang punya piutang", ["piutang", "pelanggan", "Rp"], [], 8000),
        ("di atas 100rb", ["100", "piutang", "Rp"], [], 8000),
        ("yang sudah overdue", ["overdue", "jatuh tempo", "pelanggan"], [], 8000),
        ("total piutang overdue?", ["total", "piutang", "overdue", "Rp"], [], 8000),
    ]),

    ("G_CF06_bank_filter", "filter", [
        ("transaksi bank BCA", ["BCA", "transaksi", "bank"], [], 8000),
        ("bulan ini", ["bulan ini", "transaksi", "BCA"], [], 8000),
        ("yang keluar aja", ["keluar", "debit", "transaksi"], [], 8000),
        ("di atas 500rb", ["500", "Rp", "transaksi"], [], 8000),
        ("totalnya berapa?", ["total", "Rp"], [], 8000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 8: ERROR RECOVERY (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_ER01_concept_confusion", "error_recovery", [
        ("piutang vendor Knitto", ["hutang", "vendor", "piutang", "AP", "Knitto"], [], 8000),
        ("oh iya maksudnya hutang ke Knitto", ["hutang", "Knitto", "Rp"], [], 8000),
        ("yang terbesar?", ["terbesar", "Rp", "faktur"], [], 8000),
        ("bayar dari BCA", ["bayar", "BCA", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_ER02_entity_type_mismatch", "error_recovery", [
        ("buat faktur pembelian untuk Sintia", ["Sintia", "pelanggan", "penjualan", "faktur", "pembelian"], [], 10000),
        ("oh maksudnya faktur penjualan", ["penjualan", "faktur", "Sintia", "konfirmasi"], [], 10000),
        ("poloshirt 10 pcs", ["poloshirt", "10"], [], 8000),
        ("harga 155rb", ["155", "harga"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_ER03_abort_and_query", "error_recovery", [
        ("hapus rekening BCA", ["hapus", "BCA", "rekening", "yakin", "konfirmasi"], [], 10000),
        ("eh jangan deh", ["jangan", "batal", "cancel", "oke"], [], 5000),
        ("saldo BCA berapa?", ["BCA", "saldo", "Rp"], [], 8000),
        ("aman ga saldo kita?", ["aman", "saldo", "Rp", "sehat"], [], 15000),
        ("transfer ke rekening lain bisa?", ["transfer", "rekening", "konfirmasi"], [], 10000),
    ]),

    ("G_ER04_wrong_entity", "error_recovery", [
        ("stok pisang", ["tidak", "ditemukan", "pisang", "barang"], [], 8000),
        ("eh maksudnya poloshirt", ["poloshirt", "stok", "pcs", "item"], [], 8000),
        ("stoknya berapa?", ["stok", "pcs", "poloshirt"], [], 8000),
        ("di gudang mana?", ["gudang", "warehouse", "stok"], [], 8000),
        ("perlu restock?", ["stok", "restock", "perlu", "rendah", "aman", "cukup"], [], 15000),
    ]),

    ("G_ER05_incomplete_action", "error_recovery", [
        ("buat faktur", ["faktur", "pelanggan", "siapa", "konfirmasi"], [], 10000),
        ("eh tunggu, cek piutang dulu", ["piutang", "Rp"], [], 8000),
        ("ok lanjut buat faktur Sintia", ["Sintia", "faktur", "konfirmasi"], [], 10000),
        ("poloshirt 5 pcs 150rb", ["poloshirt", "5", "150"], [], 8000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_ER06_double_cancel", "error_recovery", [
        ("buat vendor baru", ["vendor", "nama", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("batal", ["batal", "sudah", "tidak ada", "oke", "sebelumnya"], [], 5000),
        ("hutang berapa?", ["hutang", "Rp"], [], 8000),
        ("ok makasih", ["sama-sama", "terima kasih", "senang", "membantu"], [], 5000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 9: ACTION CHAINS (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_AC01_pay_then_check", "action_chain", [
        ("hutang terbesar ke siapa?", ["hutang", "Rp", "vendor", "terbesar"], [], 8000),
        ("bayar 100rb ke mereka", ["bayar", "100", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("sisa hutangnya berapa sekarang?", ["sisa", "hutang", "Rp"], [], 8000),
        ("kapan jatuh tempo sisanya?", ["jatuh tempo", "tanggal", "sisa"], [], 8000),
    ]),

    ("G_AC02_create_then_query", "action_chain", [
        ("ada pelanggan baru Ahmad", ["Ahmad", "pelanggan", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("daftar pelanggan", ["pelanggan", "customer", "daftar"], [], 8000),
        ("piutang Ahmad berapa?", ["Ahmad", "piutang", "Rp", "0", "tidak"], [], 8000),
        ("buat faktur untuk Ahmad", ["Ahmad", "faktur", "konfirmasi"], [], 10000),
    ]),

    ("G_AC03_expense_then_balance", "action_chain", [
        ("saldo BCA berapa?", ["BCA", "saldo", "Rp"], [], 8000),
        ("catat biaya listrik 500rb dari BCA", ["listrik", "500", "BCA", "konfirmasi", "biaya"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("saldo BCA sekarang berapa?", ["BCA", "saldo", "Rp"], [], 8000),
        ("turun berapa?", ["turun", "selisih", "Rp", "500"], [], 15000),
    ]),

    ("G_AC04_invoice_then_ar", "action_chain", [
        ("piutang Sintia berapa?", ["Sintia", "piutang", "Rp"], [], 8000),
        ("buat faktur untuk Sintia poloshirt 10 pcs 155rb", ["Sintia", "poloshirt", "155", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("piutang Sintia berapa sekarang?", ["Sintia", "piutang", "Rp"], [], 8000),
        ("naik berapa?", ["naik", "bertambah", "Rp", "selisih"], [], 15000),
    ]),

    ("G_AC05_void_check", "action_chain", [
        ("daftar faktur penjualan", ["faktur", "penjualan", "INV"], [], 8000),
        ("void faktur terakhir", ["void", "faktur", "batal", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("status faktur itu sekarang?", ["status", "faktur", "void", "posted", "aktif"], [], 8000),
        ("piutang berubah ga?", ["piutang", "Rp", "berubah", "turun"], [], 8000),
    ]),

    ("G_AC06_payment_then_check", "action_chain", [
        ("faktur belum lunas Sintia", ["faktur", "Sintia", "belum", "lunas", "INV"], [], 8000),
        ("bayar yang pertama", ["bayar", "faktur", "konfirmasi", "Rp"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("piutang Sintia sekarang?", ["Sintia", "piutang", "Rp"], [], 8000),
        ("berapa sisa faktur belum lunas?", ["sisa", "faktur", "belum", "lunas"], [], 8000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 10: AMBIGUOUS WITH CONTEXT (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_AX01_total_context", "ambiguous", [
        ("piutang berapa?", ["piutang", "Rp"], [], 8000),
        ("berapa totalnya?", ["total", "Rp", "piutang"], [], 8000),
        ("hutang berapa?", ["hutang", "Rp"], [], 8000),
        ("berapa totalnya?", ["total", "Rp", "hutang"], [], 8000),
        ("mana yang lebih banyak?", ["lebih", "piutang", "hutang", "besar"], [], 15000),
    ]),

    ("G_AX02_ranking_context", "ambiguous", [
        ("daftar barang", ["barang", "item", "produk"], [], 8000),
        ("mana yang paling banyak?", ["paling", "banyak", "stok", "barang"], [], 8000),
        ("daftar vendor", ["vendor", "pemasok"], [], 8000),
        ("mana yang paling banyak?", ["paling", "banyak", "vendor", "hutang"], [], 8000),
        ("bandingkan", ["bandingkan", "perbandingan", "Rp"], [], 15000),
    ]),

    ("G_AX03_print_context", "ambiguous", [
        ("laba rugi bulan ini", ["laba", "rugi", "Rp"], [], 15000),
        ("bisa diprint?", ["print", "PDF", "cetak", "download", "export"], [], 15000),
        ("daftar pelanggan", ["pelanggan", "customer"], [], 8000),
        ("export dong", ["export", "download", "cetak", "PDF"], [], 15000),
        ("kirim ke email", ["email", "kirim", "send"], [], 15000),
    ]),

    ("G_AX04_detail_context", "ambiguous", [
        ("piutang Sintia", ["Sintia", "piutang", "Rp"], [], 8000),
        ("detailnya?", ["detail", "Sintia", "faktur", "piutang"], [], 8000),
        ("hutang ke Knitto", ["Knitto", "hutang", "Rp"], [], 8000),
        ("detailnya?", ["detail", "Knitto", "faktur", "hutang"], [], 8000),
        ("mana yang lebih mendesak?", ["mendesak", "prioritas", "overdue"], [], 15000),
    ]),

    ("G_AX05_status_context", "ambiguous", [
        ("faktur Sintia", ["Sintia", "faktur", "INV"], [], 8000),
        ("statusnya?", ["status", "lunas", "belum", "posted", "draft"], [], 8000),
        ("buat biaya listrik 100rb", ["listrik", "100", "biaya", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("aman ga keuangan kita?", ["aman", "keuangan", "sehat", "Rp"], [], 15000),
    ]),

    ("G_AX06_action_context", "ambiguous", [
        ("ada hutang ke Knitto", ["Knitto", "hutang", "Rp"], [], 8000),
        ("mau bayar", ["bayar", "konfirmasi", "Rp", "rekening"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
        ("ada piutang dari Sintia", ["Sintia", "piutang", "Rp"], [], 8000),
        ("mau tagih", ["tagih", "kirim", "reminder", "konfirmasi", "Rp", "piutang"], [], 10000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 11: RAPID FIRE (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_RF01_domain_hop", "rapid_fire", [
        ("piutang", ["piutang", "Rp"], [], 8000),
        ("hutang", ["hutang", "Rp"], [], 8000),
        ("saldo", ["saldo", "Rp", "bank", "kas"], [], 8000),
        ("stok", ["stok", "barang", "item"], [], 8000),
        ("biaya", ["biaya", "pengeluaran", "Rp"], [], 8000),
    ]),

    ("G_RF02_question_words", "rapid_fire", [
        ("piutang berapa?", ["piutang", "Rp"], [], 8000),
        ("dari siapa?", ["pelanggan", "customer", "siapa"], [], 8000),
        ("kapan jatuh tempo?", ["jatuh tempo", "tanggal", "kapan"], [], 8000),
        ("sudah berapa lama?", ["lama", "hari", "overdue", "tanggal"], [], 8000),
        ("mau ditagih", ["tagih", "kirim", "bayar", "konfirmasi"], [], 10000),
    ]),

    ("G_RF03_reformat_commands", "rapid_fire", [
        ("daftar barang lengkap", ["barang", "item", "produk"], [], 8000),
        ("tabel", ["tabel", "|", "barang"], [], 8000),
        ("urutkan dari termahal", ["urut", "mahal", "harga", "Rp"], [], 8000),
        ("top 5 aja", ["5", "top", "barang"], [], 8000),
        ("total nilainya?", ["total", "nilai", "Rp"], [], 8000),
    ]),

    ("G_RF04_module_switch", "rapid_fire", [
        ("saldo BCA?", ["BCA", "saldo", "Rp"], [], 8000),
        ("piutang Sintia?", ["Sintia", "piutang", "Rp"], [], 8000),
        ("hutang Knitto?", ["Knitto", "hutang", "Rp"], [], 8000),
        ("stok poloshirt?", ["poloshirt", "stok", "pcs"], [], 8000),
        ("laba rugi?", ["laba", "rugi", "Rp"], [], 15000),
    ]),

    ("G_RF05_yes_no_rapid", "rapid_fire", [
        ("ada piutang overdue?", ["overdue", "piutang", "ada", "tidak", "belum"], [], 8000),
        ("ada hutang overdue?", ["overdue", "hutang", "ada", "tidak", "belum"], [], 8000),
        ("stok ada yang habis?", ["habis", "stok", "ada", "tidak", "barang"], [], 8000),
        ("saldo aman?", ["saldo", "aman", "Rp", "sehat"], [], 8000),
        ("posisi keuangan ok?", ["keuangan", "ok", "sehat", "aman", "baik", "Rp"], [], 15000),
    ]),

    ("G_RF06_count_barrage", "rapid_fire", [
        ("berapa pelanggan aktif?", ["pelanggan", "aktif"], [], 8000),
        ("berapa vendor aktif?", ["vendor", "aktif"], [], 8000),
        ("berapa barang aktif?", ["barang", "item", "aktif"], [], 8000),
        ("berapa rekening aktif?", ["rekening", "bank", "aktif"], [], 8000),
        ("berapa faktur belum lunas?", ["faktur", "belum", "lunas"], [], 8000),
    ]),

    # ══════════════════════════════════════════════════════════════
    # CATEGORY 12: MIXED LANGUAGE + TYPO HEAVY (30 queries, 6 groups)
    # ══════════════════════════════════════════════════════════════

    ("G_ML01_english_mix", "mixed_typo", [
        ("check piutang dong", ["piutang", "Rp"], [], 8000),
        ("list per customer", ["pelanggan", "customer", "piutang", "Rp", "list"], [], 8000),
        ("sort by amount descending", ["urut", "Rp", "terbesar", "amount"], [], 8000),
        ("yang overdue aja", ["overdue", "jatuh tempo", "Rp"], [], 8000),
        ("export to excel bisa?", ["export", "excel", "download", "belum", "tidak"], [], 15000),
    ]),

    ("G_ML02_ap_english", "mixed_typo", [
        ("total AP berapa?", ["hutang", "AP", "Rp", "payable"], [], 8000),
        ("aging reportnya?", ["aging", "AP", "hutang", "Rp", "report"], [], 8000),
        ("yang lebih dari 30 days?", ["30", "hari", "days", "Rp", "overdue"], [], 8000),
        ("vendor mana yg paling besar?", ["vendor", "terbesar", "Rp"], [], 8000),
        ("pay the largest one", ["bayar", "terbesar", "konfirmasi", "Rp", "vendor"], [], 10000),
    ]),

    ("G_ML03_typo_heavy", "mixed_typo", [
        ("stok poloshrt brp?", ["poloshirt", "stok", "pcs"], [], 8000),
        ("hrganya?", ["harga", "Rp", "poloshirt"], [], 8000),
        ("margin brp?", ["margin", "Rp", "selisih", "untung"], [], 15000),
        ("update hrga jual 200rb", ["200", "harga", "update", "ubah", "konfirmasi"], [], 10000),
        ("batal", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_ML04_indo_typo", "mixed_typo", [
        ("piutnag brapa?", ["piutang", "Rp"], [], 8000),
        ("dr pelnggan sapa?", ["pelanggan", "customer", "siapa"], [], 8000),
        ("yg plng bsar?", ["terbesar", "Rp", "pelanggan", "paling"], [], 8000),
        ("dtailnya?", ["detail", "pelanggan", "piutang", "Rp"], [], 8000),
        ("mau dbayar", ["bayar", "konfirmasi", "Rp", "piutang"], [], 10000),
    ]),

    ("G_ML05_abbreviation", "mixed_typo", [
        ("brp total AP?", ["hutang", "AP", "Rp", "total"], [], 8000),
        ("dr vendor mn?", ["vendor", "dari", "mana", "siapa"], [], 8000),
        ("plg bsr brp?", ["terbesar", "Rp", "vendor"], [], 8000),
        ("byr dr BCA bs?", ["bayar", "BCA", "konfirmasi", "rekening"], [], 10000),
        ("btl", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),

    ("G_ML06_code_switch", "mixed_typo", [
        ("show me current balance", ["saldo", "balance", "Rp", "bank"], [], 8000),
        ("any overdue invoices?", ["overdue", "faktur", "invoice", "jatuh tempo"], [], 8000),
        ("who owes us the most?", ["piutang", "pelanggan", "terbesar", "Rp", "customer"], [], 8000),
        ("create invoice for Sintia", ["Sintia", "faktur", "invoice", "konfirmasi"], [], 10000),
        ("cancel", ["batal", "cancel", "dibatalkan", "oke"], [], 5000),
    ]),
]

# Count
total_queries = sum(len(turns) for _, _, turns in GROUPS)
print(f"Total queries: {total_queries}")
print(f"Total groups: {len(GROUPS)}")

async def run_test():
    async with httpx.AsyncClient(timeout=60) as client:
        login = await client.post(f"{BASE}/api/auth/login", json={
            "email": EMAIL, "password": PASSWORD
        })
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        results = []
        cat_stats = {}
        current_cat = None

        for group_name, category, turns in GROUPS:
            if category not in cat_stats:
                cat_stats[category] = {"pass": 0, "fail": 0, "warn": 0, "slow": 0, "error": 0, "total": 0}

            # Category boundary delay
            if current_cat and current_cat != category:
                print(f"\n{'~'*70}")
                print(f"  Category switch: {current_cat} -> {category}")
                print(f"{'~'*70}")
                await asyncio.sleep(5)
            current_cat = category

            conv_id = str(uuid.uuid4())
            session_id = conv_id
            print(f"\n{'='*70}")
            print(f"GROUP: {group_name} [{category}] (conv={conv_id[:8]})")
            print(f"{'='*70}")

            for turn_idx, (query, must_contain, must_not_contain, max_lat) in enumerate(turns):
                start = time.time()
                try:
                    resp = await client.post(f"{BASE}/api/v3/chat/message", json={
                        "conversation_id": conv_id,
                        "session_id": session_id,
                        "text": query
                    }, headers=headers)
                    elapsed = int((time.time() - start) * 1000)
                    data = resp.json()

                    response_text = data.get("text", data.get("response", ""))
                    if not response_text and "data" in data:
                        response_text = str(data["data"])
                    response_lower = response_text.lower()

                    contain_pass = True
                    if must_contain:
                        contain_pass = any(kw.lower() in response_lower for kw in must_contain)

                    not_contain_pass = True
                    bad_keyword = ""
                    for kw in must_not_contain:
                        if kw.lower() in response_lower:
                            not_contain_pass = False
                            bad_keyword = kw
                            break

                    latency_pass = elapsed <= max_lat

                    if contain_pass and not_contain_pass and latency_pass:
                        status = "PASS"
                        cat_stats[category]["pass"] += 1
                    elif not not_contain_pass:
                        status = f"FAIL(has '{bad_keyword}')"
                        cat_stats[category]["fail"] += 1
                    elif not contain_pass:
                        status = "WARN(missing kw)"
                        cat_stats[category]["warn"] += 1
                    elif not latency_pass:
                        status = f"SLOW({elapsed}ms>{max_lat}ms)"
                        cat_stats[category]["slow"] += 1

                    cat_stats[category]["total"] += 1

                    lat_icon = "\U0001f7e2" if elapsed < 3000 else "\U0001f7e1" if elapsed < 8000 else "\U0001f534"
                    print(f"  T{turn_idx+1} [{status:25s}] {lat_icon} {elapsed:5d}ms Q: {query[:60]}")
                    if status != "PASS":
                        preview = response_text[:150].replace("\n", " ")
                        print(f"      -> {preview}")

                    results.append({
                        "group": group_name, "category": category, "turn": turn_idx + 1,
                        "query": query, "status": status,
                        "latency_ms": elapsed,
                        "response_preview": response_text[:300],
                        "model": data.get("model_used", "?"),
                    })

                except Exception as e:
                    elapsed = int((time.time() - start) * 1000)
                    print(f"  T{turn_idx+1} [ERROR                   ] \U0001f534 {elapsed:5d}ms Q: {query[:60]}")
                    print(f"      -> {str(e)[:100]}")
                    cat_stats[category]["error"] += 1
                    cat_stats[category]["total"] += 1
                    results.append({
                        "group": group_name, "category": category, "turn": turn_idx + 1,
                        "query": query, "status": f"ERROR: {str(e)[:80]}",
                        "latency_ms": elapsed, "response_preview": "", "model": "error",
                    })

                await asyncio.sleep(1.5)
            await asyncio.sleep(2)

        # ═══ SUMMARY ═══
        total = len(results)
        total_pass = sum(1 for r in results if r["status"] == "PASS")
        total_fail = sum(1 for r in results if r["status"].startswith("FAIL") or r["status"].startswith("ERROR"))
        total_warn = sum(1 for r in results if r["status"].startswith("WARN"))
        total_slow = sum(1 for r in results if r["status"].startswith("SLOW"))

        print(f"\n{'='*70}")
        print(f"DISCOVERY TEST RESULTS - {total} QUERIES")
        print(f"{'='*70}")
        print(f"Total:   {total}")
        print(f"PASS:    {total_pass} ({total_pass/total*100:.0f}%)")
        print(f"FAIL:    {total_fail}")
        print(f"WARN:    {total_warn}")
        print(f"SLOW:    {total_slow}")

        print(f"\nPer Category:")
        for cat_name, cs in cat_stats.items():
            ct = cs["total"]
            print(f"  {cat_name:20s}: {cs['pass']}/{ct} pass ({cs['pass']/max(ct,1)*100:.0f}%) | "
                  f"fail={cs['fail']} warn={cs['warn']} slow={cs['slow']} err={cs['error']}")

        lats = [r["latency_ms"] for r in results if "ERROR" not in r["status"]]
        if lats:
            print(f"\nLatency:")
            print(f"  Average: {sum(lats)/len(lats):.0f}ms")
            print(f"  Pipeline (<5s):  {sum(1 for l in lats if l < 5000)}")
            print(f"  Slow (5-15s):    {sum(1 for l in lats if 5000 <= l < 15000)}")
            print(f"  Agent loop (>15s): {sum(1 for l in lats if l >= 15000)}")
            print(f"  Fastest: {min(lats)}ms")
            print(f"  Slowest: {max(lats)}ms")

        print(f"\nPer-Group:")
        for group_name, category, _ in GROUPS:
            g = [r for r in results if r["group"] == group_name]
            g_pass = sum(1 for r in g if r["status"] == "PASS")
            g_avg = sum(r["latency_ms"] for r in g) / max(len(g), 1)
            icon = "OK" if g_pass == len(g) else "WARN" if g_pass >= len(g) * 0.6 else "FAIL"
            print(f"  {icon} {group_name} [{category}]: {g_pass}/{len(g)} pass, avg {g_avg:.0f}ms")

        failures = [r for r in results if r["status"] != "PASS"]
        if failures:
            print(f"\n{'='*70}")
            print(f"FAILURE DETAILS ({len(failures)} non-PASS)")
            print(f"{'='*70}")
            for r in failures:
                print(f"\n  [{r['group']}] T{r['turn']} [{r['category']}] {r['status']}")
                print(f"    Q: {r['query']}")
                print(f"    A: {r['response_preview'][:200]}")

        print(f"\n{'='*70}")
        print(f"PATTERN ANALYSIS")
        print(f"{'='*70}")

        fail_patterns = {}
        for r in failures:
            if r["status"].startswith("FAIL"):
                pattern = "wrong_content"
            elif r["status"].startswith("WARN"):
                pattern = "missing_keyword"
            elif r["status"].startswith("SLOW"):
                pattern = "slow_latency"
            elif r["status"].startswith("ERROR"):
                pattern = "error"
            else:
                pattern = "other"
            fail_patterns.setdefault(pattern, []).append(r)

        for pattern, items in fail_patterns.items():
            print(f"\n  {pattern} ({len(items)} cases):")
            for r in items:
                print(f"    - [{r['group']}] T{r['turn']} [{r['category']}]: {r['query'][:50]}")

        agent_loop = [r for r in results if r["latency_ms"] > 8000]
        if agent_loop:
            print(f"\n  Agent loop detected ({len(agent_loop)} queries >8s):")
            for r in agent_loop:
                print(f"    - [{r['group']}] T{r['turn']} {r['latency_ms']}ms: {r['query'][:50]}")

        report = {
            "summary": {
                "total": total, "pass": total_pass, "fail": total_fail,
                "warn": total_warn, "slow": total_slow,
                "pass_rate": round(total_pass/total*100, 1),
            },
            "cat_stats": {k: dict(v) for k, v in cat_stats.items()},
            "results": results,
        }
        with open("/root/milkyhoop-dev/backend/docs/reports/discovery-500.json", "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to docs/reports/discovery-500.json")

asyncio.run(run_test())
