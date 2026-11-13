# Konfig gerbang unit Gudang K1 (pill nonaktif) + K5 (urutan stabil) + K7 (detail == penentu daftar) + Barang P5 (stok per gudang, barang minus dari ledger). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bgud; CABANG=feat/gudang-k1k5k7-p5; NAMA_JENDELA=gudang
POLA_UNIT='warehouse|gudang|persediaan|items|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-gudang.sh
PENANDA_BERKAS=services/gudang_daftar.py; PENANDA='barang_minus'
A=backend/api_gateway/app; T=tests/unit/test_gudang_k.py; GDF=$A/services/gudang_daftar.py; WHF=$A/routers/warehouses.py
SAB=(
 "g1 pill nonaktif salah arah|$GDF|s#\"nonaktif\": f\"NOT {a}.is_active\"#\"nonaktif\": f\"{a}.is_active\"#|$T"
 "g2 urutan tanpa tiebreak id|$WHF|s#ORDER BY is_default DESC, name ASC, id ASC#ORDER BY is_default DESC, name ASC#|$T"
 "g3 detail tanpa kolom turunan|$WHF|s#f\"SELECT \*, {GD.kolom_turunan()} FROM warehouses WHERE id = \$1 AND tenant_id = \$2\"#\"SELECT * FROM warehouses WHERE id = \$1 AND tenant_id = \$2\"#|$T"
 "g4 predikat minus dilonggarkan (<= 0)|$GDF|s#HAVING_MINUS = \"SUM(il.quantity_in) - SUM(il.quantity_out) < 0\"#HAVING_MINUS = \"SUM(il.quantity_in) - SUM(il.quantity_out) <= 0\"#|$T"
 "g5 stok_per_gudang tanpa saring tenant|$GDF|s#WHERE il.tenant_id = \$1 AND il.product_id = \$2 GROUP BY#WHERE il.product_id = \$2 AND \$1 = \$1 GROUP BY#|$T"
 "g6 minus tak ditandai|$GDF|s#\"minus\": Decimal(str(r\[\"saldo\"\])) < 0#\"minus\": False#|$T"
 "g7 aksi_semua mengabaikan nonaktif|$GDF|s#(\"nonaktif\", nonaktif)#(\"nonaktif\", False)#|$T"
 "g8 detail tak mengisi barang_minus|$WHF|s#if row\[\"stok_minus\"\] else \[\],#if False else [],#|$T"
 "g9 detail Barang tanpa stok_per_gudang|$A/routers/items.py|s#\"stok_per_gudang\": _spg,#\"stok_per_gudang_x\": _spg,#|$T"
 "g10 daftar tak memakai turunan bersama|$WHF|s#\*\*{\*\*dict(row), \*\*GD.turunan(row)}#**dict(row)#|$T"
 "g11 barang minus dari cache warehouse_stock|$GDF|s#FROM inventory_ledger il \"\$#FROM warehouse_stock il \"#|$T"
)
