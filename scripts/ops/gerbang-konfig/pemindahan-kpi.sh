# Konfig gerbang terfokus pemindahan-kpi (19 Nov 2026): KPI /stock-transfers/summary ikut status received (Diterima). Tanpa migrasi/jurnal.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-kpi; CABANG=feat/pemindahan-kpi-diterima; NAMA_JENDELA=pemindahan-kpi
POLA_UNIT='pemindahan_kpi|stock_transfers'
HARNESS=scripts/ops/uji-salinan-pemindahan-kpi.sh
PENANDA_BERKAS=backend/api_gateway/app/routers/stock_transfers.py; PENANDA="IN ('completed','received')"
A=backend/api_gateway/app
T=tests/unit/test_pemindahan_kpi_diterima.py
SAB=(
 "k1 jumlah hanya completed|$A/routers/stock_transfers.py|s@FILTER (WHERE status IN ('completed','received') AND received_date >= \$2 AND received_date < \$3) AS selesai@FILTER (WHERE status = 'completed' AND received_date >= \$2 AND received_date < \$3) AS selesai@|$T"
 "k2 nilai hanya completed|$A/routers/stock_transfers.py|s@SUM(total_value) FILTER (WHERE status IN ('completed','received')@SUM(total_value) FILTER (WHERE status = 'completed'@|$T"
 "k3 in_transit ikut|$A/routers/stock_transfers.py|s@IN ('completed','received') AND received_date >= \$2 AND received_date < \$3) AS selesai@IN ('completed','received','in_transit') AND received_date >= \$2 AND received_date < \$3) AS selesai@|$T"
)
