"""
Dogfooding Simulation: Real Konveksi Business Conversations
10 scenarios x ~18-20 turns = ~190 queries
Natural, chaotic, typo-heavy, real-world konveksi operations.
DISCOVERY: log everything, fix nothing.
"""
import httpx, json, uuid, time, asyncio, os

BASE = "https://milkyhoop.com"
EMAIL = "grapmanado@gmail.com"
PASSWORD = "grapgrap007"

SCENARIOS = [
    (
        "S01_morning_check",
        [
            (
                "pagi, gimana kondisi hari ini?",
                [],
                [],
                15000,
                "greeting + general status",
            ),
            (
                "stok kain drill masih berapa?",
                ["stok", "kain", "drill", "tidak ditemukan", "Mohon", "tidak ada"],
                [],
                8000,
                "konveksi item",
            ),
            (
                "oh iya kain katun juga cek dong",
                ["stok", "kain", "katun", "tidak ditemukan", "Mohon", "tidak ada"],
                [],
                8000,
                "follow-up item",
            ),
            (
                "yang mana yang udah mau habis?",
                ["stok", "habis", "rendah", "tidak ada", "barang"],
                [],
                8000,
                "self-ref from bot answer",
            ),
            (
                "udah ada order masuk belum hari ini?",
                ["faktur", "order", "penjualan", "belum", "tidak"],
                [],
                10000,
                "today sales",
            ),
            (
                "piutang siapa yang belum bayar?",
                ["piutang", "pelanggan", "Rp"],
                [],
                8000,
                "AR check",
            ),
            (
                "yang paling lama nunggak siapa?",
                ["overdue", "jatuh tempo", "pelanggan", "Rp", "lama", "paling"],
                [],
                8000,
                "follow-up AR",
            ),
            ("hubungin dia bisa ga?", [], [], 8000, "unsupported — graceful"),
            (
                "ok, hutang kita ke supplier berapa total?",
                ["hutang", "Rp"],
                [],
                8000,
                "switch to AP",
            ),
            (
                "yang jatuh tempo minggu ini?",
                ["jatuh tempo", "minggu", "hutang", "tidak ada", "belum"],
                [],
                8000,
                "temporal",
            ),
            (
                "supplier kain ABC tagih lagi ga?",
                [],
                [],
                10000,
                "specific vendor — may not exist",
            ),
            (
                "brp sih hutang kita ke mereka?",
                ["hutang", "Rp", "vendor", "tidak", "Mohon"],
                [],
                8000,
                "pronoun carry",
            ),
            (
                "fakturnya nomor berapa?",
                ["faktur", "PB", "nomor", "tidak", "Mohon"],
                [],
                8000,
                "detail from context",
            ),
            (
                "bayar sebagian 5 juta dulu bisa ga?",
                ["bayar", "Rp", "konfirmasi", "tagihan", "5"],
                [],
                12000,
                "partial payment",
            ),
            ("dari rekening BCA", ["BCA", "rekening", "bank"], [], 8000, "bank slot"),
            ("batal deh", ["batal", "dibatalkan", "cancel"], [], 5000, "cancel"),
            (
                "ok sekarang stok poloshirt hitam brp?",
                ["stok", "poloshirt", "Polo", "pcs"],
                [],
                8000,
                "topic switch",
            ),
            (
                "yg putih?",
                ["stok", "putih", "poloshirt", "Polo", "pcs", "tidak"],
                [],
                8000,
                "variant",
            ),
            (
                "yg merah?",
                ["stok", "merah", "poloshirt", "Polo", "tidak"],
                [],
                8000,
                "variant",
            ),
            (
                "total semua warna poloshirt berapa?",
                ["total", "stok", "poloshirt", "Rp", "Polo", "pcs"],
                [],
                8000,
                "aggregation",
            ),
        ],
    ),
    (
        "S02_order_processing",
        [
            (
                "ada order baru dari toko maju",
                ["toko", "maju", "faktur", "penjualan", "order", "pelanggan"],
                [],
                10000,
                "start order",
            ),
            (
                "poloshirt hitam 200 pcs, putih 100 pcs",
                ["poloshirt", "200", "100", "konfirmasi", "hitam", "putih"],
                [],
                10000,
                "multi-item",
            ),
            ("harga 85rb per pcs", ["85", "harga", "Rp"], [], 8000, "price"),
            (
                "eh tunggu, harganya 80rb deng bukan 85",
                ["80", "harga", "ubah", "ganti", "koreksi"],
                [],
                8000,
                "correction",
            ),
            (
                "delivery date 2 minggu lagi",
                ["tanggal", "minggu", "delivery", "jatuh"],
                [],
                8000,
                "date slot",
            ),
            (
                "buatkan faktur penjualannya",
                ["faktur", "penjualan", "konfirmasi", "review"],
                [],
                12000,
                "create",
            ),
            ("batal dulu", ["batal", "dibatalkan", "cancel"], [], 5000, "cancel"),
            (
                "terus cek stok kain hitam cukup ga buat 200 pcs?",
                ["stok", "kain", "hitam", "tidak", "Mohon"],
                [],
                8000,
                "cross-module",
            ),
            (
                "berapa meter kain yang dibutuhin buat 200 poloshirt?",
                [],
                [],
                15000,
                "calc — may not handle",
            ),
            (
                "stok kain putih juga cek",
                ["stok", "kain", "putih", "tidak", "Mohon"],
                [],
                8000,
                "stock check",
            ),
            (
                "kayaknya kurang, pesan ke supplier dong",
                ["supplier", "vendor", "pesan", "PO", "faktur", "siapa"],
                [],
                10000,
                "transition to purchasing",
            ),
            (
                "supplier kain yang biasa siapa namanya?",
                ["vendor", "supplier", "kain", "pemasok"],
                [],
                8000,
                "vendor lookup",
            ),
            (
                "eh cek dulu faktur pembelian terakhir dari mereka",
                ["faktur", "pembelian", "PB", "terakhir", "Mohon"],
                [],
                8000,
                "historical",
            ),
            (
                "harga per meter berapa ya biasanya?",
                ["harga", "Rp", "meter"],
                [],
                8000,
                "price from history",
            ),
            (
                "sekalian cek stok benang putih",
                ["stok", "benang", "putih", "tidak", "Mohon"],
                [],
                8000,
                "stock mid-flow",
            ),
            (
                "kalau mau bikin 100 poloshirt putih butuh berapa gulung?",
                [],
                [],
                15000,
                "production calc",
            ),
        ],
    ),
    (
        "S03_payment_day",
        [
            ("hari ini mau bayar-bayar supplier", [], [], 8000, "intent"),
            (
                "cek hutang yang jatuh tempo hari ini",
                ["hutang", "jatuh tempo", "Rp", "hari", "belum", "tidak"],
                [],
                8000,
                "due today",
            ),
            (
                "urutkan dari yang paling urgent",
                ["hutang", "Rp", "tabel", "|", "urgent", "paling"],
                [],
                8000,
                "sort",
            ),
            ("saldo BCA berapa?", ["BCA", "saldo", "Rp"], [], 8000, "bank"),
            ("saldo Mandiri?", ["Mandiri", "saldo", "Rp"], [], 8000, "bank"),
            (
                "total uang kita di semua bank berapa?",
                ["total", "Rp", "bank", "saldo"],
                [],
                8000,
                "total cash",
            ),
            (
                "cukup ga buat bayar semua yang jatuh tempo?",
                ["cukup", "Rp", "hutang", "saldo"],
                [],
                15000,
                "comparison",
            ),
            (
                "kalau ga cukup bayar yang mana dulu?",
                ["bayar", "prioritas", "vendor", "Rp"],
                [],
                15000,
                "advisory",
            ),
            (
                "ok bayar yang terbesar dulu",
                ["bayar", "Rp", "konfirmasi", "tagihan", "vendor", "review"],
                [],
                12000,
                "payment",
            ),
            ("dari BCA ya", ["BCA", "rekening"], [], 8000, "bank slot"),
            ("batal", ["batal", "dibatalkan", "cancel"], [], 5000, "cancel"),
            (
                "sisa hutang jatuh tempo berapa?",
                ["sisa", "hutang", "Rp", "jatuh", "tempo"],
                [],
                8000,
                "post-action",
            ),
            (
                "besok ada yang jatuh tempo lagi?",
                ["besok", "jatuh tempo", "hutang", "tidak", "belum"],
                [],
                8000,
                "temporal",
            ),
            (
                "total hutang kita keseluruhan berapa sih?",
                ["total", "hutang", "Rp"],
                [],
                8000,
                "total AP",
            ),
            (
                "bandingkan sama piutang",
                ["piutang", "hutang", "Rp"],
                [],
                10000,
                "comparison",
            ),
            (
                "kita net positif atau negatif?",
                ["net", "positif", "negatif", "Rp", "piutang", "hutang"],
                [],
                15000,
                "analysis",
            ),
            (
                "siapa pelanggan yang piutangnya paling gede?",
                ["pelanggan", "piutang", "Rp", "terbesar", "paling"],
                [],
                8000,
                "switch to AR",
            ),
            (
                "udah jatuh tempo belum?",
                ["jatuh tempo", "overdue", "belum", "sudah"],
                [],
                8000,
                "follow-up",
            ),
            ("tagih dia dong kirim reminder", [], [], 8000, "unsupported"),
            ("ok bot ga bisa kirim WA ya noted", [], [], 5000, "acknowledgment"),
        ],
    ),
    (
        "S04_production_cost",
        [
            (
                "biaya produksi bulan ini berapa total?",
                ["biaya", "produksi", "pengeluaran", "Rp"],
                [],
                10000,
                "expense",
            ),
            (
                "rinciannya per kategori dong",
                ["biaya", "kategori", "Rp", "tabel", "|", "akun", "beban"],
                [],
                8000,
                "breakdown",
            ),
            (
                "kain berapa benang berapa sablon berapa",
                ["kain", "benang", "sablon", "Rp", "biaya"],
                [],
                10000,
                "multi-cat",
            ),
            (
                "bordir bulan ini ada berapa kali?",
                ["bordir", "transaksi", "kali", "biaya"],
                [],
                10000,
                "cat count",
            ),
            (
                "total biaya bordir?",
                ["bordir", "total", "Rp", "biaya"],
                [],
                8000,
                "cat total",
            ),
            (
                "vendor bordir kita siapa aja?",
                ["vendor", "bordir", "pemasok"],
                [],
                8000,
                "vendor by cat",
            ),
            (
                "yang paling murah?",
                ["murah", "vendor", "Rp", "termurah", "paling"],
                [],
                8000,
                "ranking",
            ),
            (
                "hmm kalau dibanding bulan lalu gimana?",
                ["bulan lalu", "bulan", "Rp", "biaya"],
                [],
                15000,
                "temporal compare",
            ),
            (
                "biaya naik atau turun?",
                ["naik", "turun", "Rp", "biaya"],
                [],
                15000,
                "trend",
            ),
            (
                "kategori mana yang naik paling banyak?",
                ["kategori", "naik", "Rp", "biaya", "paling"],
                [],
                15000,
                "cat trend",
            ),
            (
                "oh kain naik ya, supplier mana yang naikin harga?",
                ["supplier", "vendor", "kain", "harga"],
                [],
                15000,
                "cross-module",
            ),
            (
                "cek faktur pembelian kain bulan ini vs bulan lalu",
                ["faktur", "pembelian", "kain", "bulan"],
                [],
                15000,
                "historical",
            ),
            ("selisihnya berapa?", ["selisih", "Rp"], [], 15000, "calc"),
            (
                "mau ganti supplier bisa ga cek vendor kain yang lain",
                ["vendor", "kain", "supplier", "pemasok"],
                [],
                10000,
                "vendor search",
            ),
            (
                "ada berapa vendor kain di sistem?",
                ["vendor", "kain", "jumlah", "berapa"],
                [],
                8000,
                "vendor count",
            ),
        ],
    ),
    (
        "S05_end_of_day",
        [
            (
                "rekap penjualan hari ini dong",
                ["penjualan", "Rp", "faktur", "hari"],
                [],
                10000,
                "daily recap",
            ),
            (
                "ada berapa faktur dibuat?",
                ["faktur", "buah", "transaksi", "jumlah"],
                [],
                8000,
                "count",
            ),
            (
                "total omzet?",
                ["total", "omzet", "penjualan", "Rp"],
                [],
                8000,
                "total sales",
            ),
            (
                "bandingkan sama kemarin",
                ["kemarin", "Rp", "penjualan"],
                [],
                15000,
                "temporal",
            ),
            ("naik atau turun?", ["naik", "turun", "Rp"], [], 15000, "trend"),
            (
                "customer siapa yang paling banyak order?",
                ["customer", "pelanggan", "terbanyak", "Rp", "paling"],
                [],
                8000,
                "ranking",
            ),
            (
                "dia beli apa aja?",
                ["beli", "barang", "faktur", "poloshirt", "item"],
                [],
                8000,
                "self-ref 'dia'",
            ),
            (
                "pengeluaran hari ini berapa?",
                ["pengeluaran", "biaya", "Rp"],
                [],
                8000,
                "expense",
            ),
            (
                "biaya terbesar untuk apa?",
                ["biaya", "terbesar", "Rp", "akun", "beban"],
                [],
                8000,
                "ranking",
            ),
            (
                "laba kotor hari ini berapa kira-kira?",
                ["laba", "Rp", "pendapatan"],
                [],
                15000,
                "profit calc",
            ),
            (
                "piutang yang masuk hari ini ada?",
                ["piutang", "pembayaran", "hari", "tidak", "belum"],
                [],
                10000,
                "receipts",
            ),
            (
                "siapa yang bayar?",
                ["pelanggan", "bayar", "Rp", "tidak", "belum"],
                [],
                8000,
                "self-ref",
            ),
            ("berapa?", ["Rp", "bayar", "total"], [], 8000, "ultra-short"),
            (
                "sisa piutang total sekarang?",
                ["piutang", "total", "Rp"],
                [],
                8000,
                "current AR",
            ),
            (
                "ok closing. posisi keuangan kita gimana?",
                ["keuangan", "Rp", "piutang", "hutang", "kas", "saldo"],
                [],
                15000,
                "financial health",
            ),
        ],
    ),
    (
        "S06_inventory_problem",
        [
            (
                "stok kain yang di bawah minimum ada ga?",
                ["stok", "minimum", "rendah", "kain", "barang", "tidak"],
                [],
                8000,
                "low stock",
            ),
            (
                "wah banyak juga, mana yang paling urgent?",
                ["stok", "urgent", "rendah", "paling"],
                [],
                8000,
                "self-ref",
            ),
            (
                "kain drill tinggal berapa?",
                ["kain", "drill", "stok", "Mohon", "tidak"],
                [],
                8000,
                "specific item",
            ),
            (
                "order yang pending butuh berapa meter drill?",
                [],
                [],
                15000,
                "cross-ref",
            ),
            (
                "jadi kurang berapa?",
                ["kurang", "selisih", "meter"],
                [],
                15000,
                "calc from context",
            ),
            (
                "supplier drill siapa?",
                ["supplier", "vendor", "drill", "kain", "pemasok", "Mohon"],
                [],
                8000,
                "vendor for material",
            ),
            ("bisa kirim berapa hari?", [], [], 10000, "lead time — unsupported"),
            (
                "ok pesan 1000 meter drill",
                ["pesan", "PO", "faktur", "1000", "konfirmasi", "drill"],
                [],
                12000,
                "create PO",
            ),
            ("harga berapa per meter?", ["harga", "Rp", "meter"], [], 8000, "price"),
            (
                "cek harga terakhir beli",
                ["harga", "terakhir", "faktur", "pembelian", "Rp", "tidak"],
                [],
                8000,
                "historical price",
            ),
            (
                "masih sama ga harganya?",
                ["harga", "sama", "Rp", "naik", "turun"],
                [],
                15000,
                "price comparison",
            ),
            (
                "total PO jadi berapa?",
                ["total", "PO", "Rp", "faktur"],
                [],
                8000,
                "total calc",
            ),
        ],
    ),
    (
        "S07_customer_mgmt",
        [
            (
                "pelanggan kita ada berapa total?",
                ["pelanggan", "customer", "total", "jumlah"],
                [],
                8000,
                "count",
            ),
            (
                "yang aktif berapa?",
                ["aktif", "pelanggan", "customer"],
                [],
                8000,
                "active count",
            ),
            (
                "yang paling banyak order siapa?",
                ["pelanggan", "order", "terbanyak", "Rp", "paling"],
                [],
                8000,
                "top customer",
            ),
            (
                "detail pelanggan itu dong",
                ["pelanggan", "detail", "telepon", "alamat", "email"],
                [],
                8000,
                "self-ref 'itu'",
            ),
            (
                "total transaksi dia berapa tahun ini?",
                ["transaksi", "total", "Rp"],
                [],
                10000,
                "tx total",
            ),
            (
                "piutang dia berapa?",
                ["piutang", "Rp", "outstanding"],
                [],
                8000,
                "customer AR",
            ),
            (
                "payment terms-nya berapa hari?",
                ["payment", "terms", "hari"],
                [],
                10000,
                "may not be stored",
            ),
            (
                "ada yang telat bayar?",
                ["telat", "overdue", "jatuh tempo", "pelanggan"],
                [],
                8000,
                "overdue",
            ),
            (
                "daftar pelanggan yang overdue",
                ["pelanggan", "overdue", "jatuh tempo", "Rp"],
                [],
                8000,
                "overdue list",
            ),
            (
                "ok ada pelanggan baru mau didaftarin",
                ["pelanggan", "baru", "nama", "tambah"],
                [],
                10000,
                "create customer",
            ),
            (
                "namanya CV Seragam Jaya dari Semarang",
                ["Seragam Jaya", "CV", "Semarang", "konfirmasi", "review"],
                [],
                10000,
                "details",
            ),
            (
                "telepon 024-1234567",
                ["024", "telepon", "konfirmasi"],
                [],
                8000,
                "phone slot",
            ),
            (
                "batal deh ga jadi",
                ["batal", "dibatalkan", "cancel"],
                [],
                5000,
                "cancel",
            ),
            (
                "mereka mau order seragam sekolah 500 pcs",
                ["seragam", "500", "order", "faktur", "penjualan"],
                [],
                10000,
                "immediate order",
            ),
            ("harga 95rb per pcs", ["95", "harga", "Rp"], [], 8000, "price"),
            (
                "buatkan faktur penjualannya",
                ["faktur", "penjualan", "konfirmasi", "review"],
                [],
                12000,
                "create invoice",
            ),
            ("batal", ["batal", "dibatalkan", "cancel"], [], 5000, "cancel"),
            (
                "sisa piutang dia berapa?",
                ["piutang", "sisa", "Rp", "Mohon", "pelanggan"],
                [],
                8000,
                "remaining AR",
            ),
        ],
    ),
    (
        "S08_monthly_closing",
        [
            ("udah akhir bulan nih bantu tutup buku dong", [], [], 10000, "opening"),
            (
                "laba rugi bulan ini gimana?",
                ["laba", "rugi", "pendapatan", "beban", "Rp"],
                [],
                15000,
                "P&L",
            ),
            (
                "pendapatan total berapa?",
                ["pendapatan", "total", "Rp"],
                [],
                8000,
                "revenue",
            ),
            (
                "beban terbesar apa?",
                ["beban", "terbesar", "Rp", "akun"],
                [],
                8000,
                "top expense",
            ),
            (
                "margin bruto berapa persen?",
                ["margin", "bruto", "persen", "%", "Rp"],
                [],
                15000,
                "gross margin",
            ),
            (
                "bandingkan sama bulan lalu",
                ["bulan lalu", "Rp", "laba", "rugi", "bulan"],
                [],
                15000,
                "compare",
            ),
            (
                "neraca gimana?",
                ["neraca", "aset", "kewajiban", "ekuitas", "Rp"],
                [],
                15000,
                "balance sheet",
            ),
            (
                "aset lancar berapa?",
                ["aset", "lancar", "Rp"],
                [],
                10000,
                "current assets",
            ),
            (
                "kewajiban berapa?",
                ["kewajiban", "Rp", "liabilitas"],
                [],
                10000,
                "liabilities",
            ),
            (
                "ekuitas naik atau turun?",
                ["ekuitas", "naik", "turun", "Rp"],
                [],
                15000,
                "equity trend",
            ),
            (
                "arus kas bulan ini gimana?",
                ["arus kas", "cash flow", "Rp"],
                [],
                15000,
                "cash flow",
            ),
            (
                "stok opname — total nilai persediaan berapa?",
                ["stok", "persediaan", "nilai", "Rp", "inventori"],
                [],
                10000,
                "inventory value",
            ),
            (
                "hutang piutang summary dong",
                ["hutang", "piutang", "Rp", "summary", "ringkasan"],
                [],
                10000,
                "AR/AP summary",
            ),
            (
                "net-nya kita plus atau minus?",
                ["net", "plus", "minus", "Rp", "positif", "negatif"],
                [],
                15000,
                "net position",
            ),
            ("ok cetak semua laporan", [], [], 8000, "print — unsupported"),
            ("export ke excel bisa?", [], [], 8000, "export — unsupported"),
            (
                "ya udah ringkasan aja deh yang penting-penting",
                ["ringkasan", "Rp", "summary"],
                [],
                15000,
                "summary",
            ),
        ],
    ),
    (
        "S09_troubleshoot",
        [
            ("eh ada masalah nih", [], [], 5000, "problem start"),
            ("customer komplain barang salah kirim", [], [], 8000, "complaint context"),
            (
                "cek faktur terakhir ke toko maju",
                ["faktur", "toko", "maju", "penjualan", "INV"],
                [],
                8000,
                "invoice lookup",
            ),
            (
                "isinya apa aja?",
                ["barang", "item", "poloshirt", "pcs", "Rp"],
                [],
                8000,
                "self-ref: invoice content",
            ),
            (
                "harusnya poloshirt hitam tapi yang dikirim putih",
                [],
                [],
                8000,
                "context",
            ),
            (
                "stok poloshirt hitam masih ada ga?",
                ["stok", "poloshirt", "hitam", "Polo", "pcs"],
                [],
                8000,
                "stock check",
            ),
            ("berapa?", ["stok", "pcs", "Rp"], [], 8000, "ultra-short"),
            (
                "ok kirim pengganti catat retur dulu",
                [],
                [],
                10000,
                "return — may not support",
            ),
            (
                "void faktur yang salah bisa ga?",
                ["void", "faktur", "batal", "konfirmasi", "review"],
                [],
                10000,
                "void invoice",
            ),
            (
                "batal deh jangan void",
                ["batal", "jangan", "dibatalkan"],
                [],
                5000,
                "cancel void",
            ),
            (
                "bikin faktur baru yang benar",
                ["faktur", "penjualan", "baru", "konfirmasi"],
                [],
                12000,
                "new invoice",
            ),
            (
                "poloshirt hitam 200 pcs 80rb",
                ["poloshirt", "hitam", "200", "80", "konfirmasi"],
                [],
                10000,
                "item details",
            ),
            ("ke toko maju", ["toko", "maju", "pelanggan"], [], 8000, "customer slot"),
            ("batal", ["batal", "dibatalkan", "cancel"], [], 5000, "cancel"),
            (
                "total stok poloshirt semua warna berapa?",
                ["stok", "poloshirt", "total", "Polo", "pcs"],
                [],
                8000,
                "aggregation",
            ),
            (
                "hutang ke vendor benang jatuh tempo hari ini ga?",
                ["hutang", "vendor", "benang", "jatuh tempo", "tidak", "belum"],
                [],
                8000,
                "topic switch",
            ),
        ],
    ),
    (
        "S10_quick_fire",
        [
            ("piutang?", ["piutang", "Rp"], [], 8000, "single word"),
            ("hutang?", ["hutang", "Rp"], [], 8000, "single word"),
            (
                "mana lebih gede?",
                ["Rp", "piutang", "hutang", "lebih", "besar"],
                [],
                10000,
                "comparison",
            ),
            ("stok?", ["stok", "barang", "item"], [], 8000, "single word"),
            (
                "yg habis?",
                ["habis", "stok", "kosong", "0", "tidak"],
                [],
                8000,
                "follow-up",
            ),
            ("BCA brp?", ["BCA", "saldo", "Rp"], [], 8000, "abbreviation"),
            (
                "cukup buat byr hutang?",
                ["cukup", "hutang", "saldo", "Rp"],
                [],
                15000,
                "calc",
            ),
            (
                "faktur terakhir?",
                ["faktur", "INV", "PB", "terakhir", "penjualan", "pembelian"],
                [],
                8000,
                "recent",
            ),
            (
                "buat faktur ke sintia poloshirt 50 pcs 90rb",
                ["faktur", "Sintia", "poloshirt", "50", "90", "konfirmasi", "review"],
                [],
                12000,
                "full create",
            ),
            ("eh cancel", ["batal", "cancel", "dibatalkan"], [], 5000, "cancel"),
            (
                "buat ke toko maju aja",
                ["toko", "maju", "faktur", "konfirmasi", "penjualan"],
                [],
                10000,
                "restart",
            ),
            (
                "poloshirt hitam 100 pcs",
                ["poloshirt", "100", "hitam"],
                [],
                8000,
                "item slot",
            ),
            ("harga 80rb", ["80", "harga", "Rp"], [], 8000, "price slot"),
            ("batal", ["batal", "cancel", "dibatalkan"], [], 5000, "cancel again"),
            (
                "piutang toko maju skrg brp?",
                ["piutang", "toko", "maju", "Rp", "Mohon"],
                [],
                8000,
                "post-action",
            ),
            (
                "total omzet bulan ini?",
                ["omzet", "penjualan", "total", "Rp"],
                [],
                8000,
                "total sales",
            ),
            ("udah target belum?", [], [], 8000, "unsupported"),
            (
                "ok thx",
                ["sama-sama", "terima kasih", "senang", "membantu"],
                [],
                5000,
                "closing",
            ),
        ],
    ),
]


async def run_test():
    async with httpx.AsyncClient(timeout=60) as client:
        login = await client.post(
            f"{BASE}/api/auth/login",
            json={"email": EMAIL, "password": PASSWORD, "tenant_slug": "grapgrap"},
        )
        token = login.json()["data"]["access_token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        token_time = time.time()

        results = []
        scenario_stats = {}
        total_queries = sum(len(t) for _, t in SCENARIOS)

        print(f"\n{'='*70}")
        print("  DOGFOODING SIMULATION — KONVEKSI")
        print(f"  Target: {BASE}/api/v3/chat/message")
        print(f"  Scenarios: {len(SCENARIOS)} | Queries: {total_queries}")
        print(f"{'='*70}")

        for si, (scenario_name, turns) in enumerate(SCENARIOS):
            conv_id = str(uuid.uuid4())
            stats = {"pass": 0, "fail": 0, "warn": 0, "slow": 0, "error": 0}

            print(f"\n{'='*70}")
            print(
                f"  SCENARIO {si+1}/{len(SCENARIOS)}: {scenario_name} (conv={conv_id[:8]})"
            )
            print(f"{'='*70}")

            for ti, (query, must_have, must_not, max_ms, note) in enumerate(turns):
                # Token refresh
                if time.time() - token_time > 25:
                    try:
                        r2 = await client.post(
                            f"{BASE}/api/auth/login",
                            json={
                                "email": EMAIL,
                                "password": PASSWORD,
                                "tenant_slug": "grapgrap",
                            },
                        )
                        token = r2.json()["data"]["access_token"]
                        headers["Authorization"] = f"Bearer {token}"
                        token_time = time.time()
                    except:
                        pass

                start = time.time()
                try:
                    resp = await client.post(
                        f"{BASE}/api/v3/chat/message",
                        json={
                            "conversation_id": conv_id,
                            "session_id": conv_id,
                            "text": query,
                        },
                        headers=headers,
                    )
                    ms = int((time.time() - start) * 1000)

                    if resp.status_code != 200:
                        st = f"HTTP_{resp.status_code}"
                        txt = resp.text[:200]
                        stats["error"] += 1
                        print(f"  T{ti+1:2d} [{st:20s}] 🔴 {ms:5d}ms Q: {query[:55]}")
                        results.append(
                            {
                                "scenario": scenario_name,
                                "turn": ti + 1,
                                "query": query,
                                "status": st,
                                "latency_ms": ms,
                                "note": note,
                                "response": txt[:400],
                            }
                        )
                        await asyncio.sleep(1.5)
                        continue

                    data = resp.json()
                    txt = data.get(
                        "text", data.get("response", str(data.get("data", "")))
                    )
                    msg_type = data.get("message_type", "?")
                    lo = txt.lower()

                    ok_have = (
                        any(k.lower() in lo for k in must_have) if must_have else True
                    )
                    bad = next((k for k in must_not if k.lower() in lo), None)
                    ok_not = bad is None
                    ok_lat = ms <= max_ms

                    if ok_have and ok_not and ok_lat:
                        st = "PASS"
                        stats["pass"] += 1
                    elif bad:
                        st = f"FAIL('{bad}')"
                        stats["fail"] += 1
                    elif not ok_have:
                        st = "WARN(kw)"
                        stats["warn"] += 1
                    else:
                        st = f"SLOW({ms}ms)"
                        stats["slow"] += 1

                    icon = "🟢" if ms < 3000 else "🟡" if ms < 8000 else "🔴"
                    print(
                        f"  T{ti+1:2d} [{st:20s}] {icon} {ms:5d}ms [{msg_type:15s}] Q: {query[:55]}"
                    )
                    if st != "PASS":
                        print(f"       → {txt[:140].replace(chr(10),' ')}")
                        print(f"       note: {note}")

                    results.append(
                        {
                            "scenario": scenario_name,
                            "turn": ti + 1,
                            "query": query,
                            "status": st,
                            "latency_ms": ms,
                            "note": note,
                            "message_type": msg_type,
                            "response": txt[:400],
                        }
                    )

                except Exception as e:
                    ms = int((time.time() - start) * 1000)
                    print(
                        f"  T{ti+1:2d} [ERROR               ] 🔴 {ms:5d}ms Q: {query[:55]}"
                    )
                    print(f"       → {e}")
                    stats["error"] += 1
                    results.append(
                        {
                            "scenario": scenario_name,
                            "turn": ti + 1,
                            "query": query,
                            "status": f"ERROR:{e}",
                            "latency_ms": ms,
                            "note": note,
                            "response": "",
                        }
                    )

                await asyncio.sleep(1.5)

            scenario_stats[scenario_name] = stats
            await asyncio.sleep(3)

        # ═══ SUMMARY ═══
        total = len(results)
        tp = sum(s["pass"] for s in scenario_stats.values())
        tf = sum(s["fail"] for s in scenario_stats.values())
        tw = sum(s["warn"] for s in scenario_stats.values())
        ts = sum(s["slow"] for s in scenario_stats.values())
        te = sum(s["error"] for s in scenario_stats.values())

        lats = [r["latency_ms"] for r in results if not r["status"].startswith("ERROR")]

        print(f"\n{'='*70}")
        print("  DOGFOODING SIMULATION — FINAL REPORT")
        print(f"{'='*70}")
        print(f"  Total: {total} queries across {len(SCENARIOS)} scenarios")
        print(f"  PASS: {tp} ({tp/total*100:.0f}%)")
        print(f"  FAIL: {tf} | WARN: {tw} | SLOW: {ts} | ERROR: {te}")
        if lats:
            print(
                f"  Latency: avg {sum(lats)//len(lats)}ms, "
                f"<3s: {sum(1 for l in lats if l<3000)}, "
                f"3-8s: {sum(1 for l in lats if 3000<=l<8000)}, "
                f"8-15s: {sum(1 for l in lats if 8000<=l<15000)}, "
                f">15s: {sum(1 for l in lats if l>=15000)}"
            )

        print("\n  Per-Scenario:")
        for sn, ss in scenario_stats.items():
            st = ss["pass"] + ss["fail"] + ss["warn"] + ss["slow"] + ss["error"]
            icon = "✅" if ss["pass"] == st else "⚠️" if ss["pass"] >= st * 0.6 else "❌"
            print(f"    {icon} {sn}: {ss['pass']}/{st} pass")

        failures = [r for r in results if r["status"] != "PASS"]
        if failures:
            print(f"\n{'='*70}")
            print(f"  NON-PASS DETAILS ({len(failures)})")
            print(f"{'='*70}")
            for r in failures:
                resp = r.get("response", "")[:180].replace(chr(10), " ")
                print(f"\n  [{r['scenario']}] T{r['turn']} {r['status']}")
                print(f"    Q: {r['query']}")
                print(f"    A: {resp}")
                print(f"    Note: {r['note']}")

        # ═══ PATTERN CLUSTERING ═══
        patterns = {
            "ENTITY_FROM_OWN_RESPONSE": [],
            "ENTITY_CARRY_FORWARD": [],
            "KONVEKSI_VOCAB_MISS": [],
            "CROSS_MODULE_BREAK": [],
            "TEMPORAL_FAIL": [],
            "COMPARISON_FAIL": [],
            "UNSUPPORTED_GRACEFUL": [],
            "NUMERICAL_REASONING": [],
            "EMPTY_RESPONSE": [],
            "AGENT_LOOP_SLOW": [],
            "OTHER": [],
        }

        for r in failures:
            q = r["query"].lower()
            resp = r.get("response", "").lower()
            turn = r["turn"]
            classified = False

            if resp.strip() == "":
                patterns["EMPTY_RESPONSE"].append(r)
                classified = True
            elif any(
                w in q
                for w in [
                    "export",
                    "excel",
                    "print",
                    "cetak",
                    "kirim wa",
                    "kirim reminder",
                    "hubungin",
                ]
            ):
                patterns["UNSUPPORTED_GRACEFUL"].append(r)
                classified = True
            elif (
                "mohon sebutkan" in resp
                or "tidak ditemukan" in resp
                or "tidak menemukan" in resp
            ):
                if any(
                    w in q
                    for w in ["kain", "benang", "sablon", "bordir", "drill", "katun"]
                ):
                    patterns["KONVEKSI_VOCAB_MISS"].append(r)
                    classified = True
                elif any(
                    w in q
                    for w in [
                        "yang pertama",
                        "yang terakhir",
                        "yang terbesar",
                        "itu ",
                        "isinya",
                    ]
                ):
                    patterns["ENTITY_FROM_OWN_RESPONSE"].append(r)
                    classified = True
                elif any(
                    w in q
                    for w in ["dia ", "mereka ", "nya?", "nya ", "di situ", "ke mereka"]
                ):
                    patterns["ENTITY_CARRY_FORWARD"].append(r)
                    classified = True
                elif turn >= 2:
                    patterns["ENTITY_CARRY_FORWARD"].append(r)
                    classified = True
            elif any(
                w in q
                for w in [
                    "margin",
                    "selisih",
                    "cukup",
                    "ratio",
                    "persen",
                    "net ",
                    "kurang",
                    "lebih gede",
                ]
            ):
                patterns["NUMERICAL_REASONING"].append(r)
                classified = True
            elif any(
                w in q for w in ["kemarin", "bulan lalu", "bandingkan", "tren", "vs "]
            ):
                patterns["TEMPORAL_FAIL"].append(r)
                classified = True
            elif r["status"].startswith("SLOW"):
                patterns["AGENT_LOOP_SLOW"].append(r)
                classified = True

            if not classified:
                patterns["OTHER"].append(r)

        print(f"\n{'='*70}")
        print("  PATTERN CLUSTERS")
        print(f"{'='*70}")
        for name, items in sorted(patterns.items(), key=lambda x: -len(x[1])):
            if items:
                scenarios_hit = set(r["scenario"] for r in items)
                print(f"\n  📌 {name} — {len(items)} failures")
                print(f"     Scenarios: {', '.join(sorted(scenarios_hit))}")
                for ex in items[:4]:
                    resp_short = ex.get("response", "")[:100].replace(chr(10), " ")
                    print(f"     • T{ex['turn']}: \"{ex['query'][:60]}\"")
                    print(f"       → {resp_short}")

        os.makedirs("/root/milkyhoop-dev/backend/docs/reports", exist_ok=True)
        with open(
            "/root/milkyhoop-dev/backend/docs/reports/dogfood-konveksi.json", "w"
        ) as f:
            json.dump(
                {
                    "summary": {
                        "total": total,
                        "pass": tp,
                        "fail": tf,
                        "warn": tw,
                        "slow": ts,
                        "error": te,
                        "pass_rate": round(tp / total * 100, 1),
                    },
                    "scenarios": scenario_stats,
                    "patterns": {k: len(v) for k, v in patterns.items() if v},
                    "results": results,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print("\n  📁 Saved to docs/reports/dogfood-konveksi.json")


asyncio.run(run_test())
