# Konfig gerbang unit BUG-249 (nama pelanggan di Cek/Giro detail + Biaya daftar/detail dibaca dari customers.nama; customers.name NULL di semua baris). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi.
W=/root/mh-b249; CABANG=fix/bug249-customers-nama; NAMA_JENDELA=b249
POLA_UNIT='bug249|customers_nama|cheque|expense|biaya|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b249.sh
PENANDA_BERKAS=routers/cheques.py; PENANDA='cu.nama as customer_name'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug249_customers_nama.py
SAB=(
 "n1 Cek detail kembali ke cu.name|$A/routers/cheques.py|s#cu.nama as customer_name#cu.name as customer_name#|$T"
 "n2 Biaya daftar kembali ke cu.name|$A/routers/expenses.py|s#SELECT cu.nama FROM customers cu#SELECT cu.name FROM customers cu#|$T"
 "n3 Biaya detail kembali ke name|$A/routers/expenses.py|s#\"SELECT nama FROM customers WHERE id#\"SELECT name FROM customers WHERE id#|$T"
)
