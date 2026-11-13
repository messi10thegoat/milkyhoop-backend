# Konfig gerbang unit K9 (Kartu Stok per gudang: kolom Gudang, filter gudang/periode, saldo awal, ringkasan; Perbedaan Persediaan Terkirim dari ledger). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-bk9; CABANG=feat/kartu-stok-gudang-perbedaan-terkirim; NAMA_JENDELA=k9
POLA_UNIT='items|history|stock_transfer|pemindahan|kartu_stok|sql_placeholder|izin_rute|inventaris_rute|pagar_rute|permission|nilai_hpp'
HARNESS=scripts/ops/uji-salinan-k9.sh
PENANDA_BERKAS=routers/stock_transfers.py; PENANDA='get_transfer_variance'
A=backend/api_gateway/app; T=tests/unit/test_k9.py; ITF=$A/routers/items.py; STF=$A/routers/stock_transfers.py
SAB=(
 "h1 filter gudang diabaikan|$ITF|s#saring.append(f\"il.warehouse_id = \${len(params)}::uuid\")#pass#|$T"
 "h2 saldo berjalan tak mulai dari saldo awal|$ITF|s#(row\[\"saldo_berjalan\"\] or 0) + saldo_awal,#(row[\"saldo_berjalan\"] or 0) + 0,#|$T"
 "h3 saldo awal ikut tanggal mulai (<=)|$ITF|s#AND il.movement_date < \${len(awal_params)}#AND il.movement_date <= \${len(awal_params)}#|$T"
 "h4 kolom Gudang tak di-join|$ITF|s#            LEFT JOIN warehouses w ON w.id = il.warehouse_id AND w.tenant_id = il.tenant_id##|$T"
 "h5 ringkasan tak dihitung|$ITF|s#        if wid or df or dt:#        if False:#|$T"
 "h6 sampai-tanggal eksklusif|$ITF|s#movement_date <= \${len(params)}#movement_date < \${len(params)}#|$T"
 "h7 perbedaan: diterima dari kolom beku (bukan ledger)|$STF|s#d.diterima AS quantity_received#i.quantity_received AS quantity_received#|$T"
 "h8 perbedaan: saringan tertunda hilang|$STF|s#AND COALESCE(i.quantity_shipped, 0) > d.diterima#AND 1=1#|$T"
 "h9 perbedaan: status bukan in_transit ikut|$STF|s#AND st.status = 'in_transit'#AND st.status <> 'cancelled'#|$T"
 "h10 perbedaan: izin rute hilang|$A/middleware/permission_middleware.py|s#(summary|in-transit|variance)#(summary|in-transit)#|$T"
 "h11 perbedaan: nilai tertunda salah|$STF|s#nilai = (tertunda \* biaya).quantize(Decimal(\"0.01\"))#nilai = tertunda#|$T"
 "h12 perbedaan: tertunda tak dikurangi diterima|$STF|s#tertunda = Decimal(str(r\[\"quantity_shipped\"\] or 0)) - Decimal(str(r\[\"quantity_received\"\] or 0))#tertunda = Decimal(str(r[\"quantity_shipped\"] or 0))#|$T"
)
