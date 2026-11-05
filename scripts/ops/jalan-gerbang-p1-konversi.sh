#!/usr/bin/env bash
# P1 konversi terkunci (putusan pemilik LANGSUNG 5 Nov): SEMUA tes beririsan + SQL penentu dijalankan BACA SAJA di prod (kaos) + sabotase
# (penentu selalu lolos -> tes WAJIB merah).
W=${W:-/root/mh-p1}; cd $W && git fetch -q deploy && echo "HEAD $(git rev-parse --short HEAD) master $(git rev-parse --short deploy/master)"
L=/root/logs/p1-$(date -u +%Y%m%dT%H%M%S); mkdir -p $L; echo "log: $L"
F="tests/unit/test_aturan_ubah_tipe.py tests/unit/test_chat_pemilik_sesi.py tests/unit/test_dp_status_detail.py tests/unit/test_harga_jual.py tests/unit/test_hpp_neto.py tests/unit/test_item_aliases.py tests/unit/test_item_field_utuh.py tests/unit/test_item_nama_jenis.py tests/unit/test_item_sku_kode_status.py tests/unit/test_kirim_non_stok.py tests/unit/test_konversi_terkunci.py tests/unit/test_milkybook_t0.py tests/unit/test_nama_barang_409.py tests/unit/test_nd_gudang.py tests/unit/test_nk_teks.py tests/unit/test_nota_debit_u3c.py tests/unit/test_p2p6a_teks_pembelian_id.py tests/unit/test_pemindahan_barang.py tests/unit/test_q018_akui_pendapatan_nonstok.py tests/unit/test_qty_dasar.py tests/unit/test_qty_pdf_dan_sj_faktur.py tests/unit/test_rp_draf_cek_sisa.py tests/unit/test_rp_draf_pratinjau.py tests/unit/test_rupiah_satu_pemformat.py tests/unit/test_saldo_awal_stok.py tests/unit/test_si_fpb_rp0.py tests/unit/test_si_law.py tests/unit/test_so_kirim_pratinjau.py tests/unit/test_sql_placeholder_mentah.py tests/unit/test_teks_galat_penjualan.py tests/unit/test_teks_periode_id.py tests/unit/test_terima_tabel.py tests/unit/test_u11_status_detail_nk.py tests/unit/test_u2_proforma.py tests/unit/test_u3a_b_reverse.py tests/unit/test_u_kode_teks_rp.py tests/unit/test_ump_po_a.py "
timeout 1200 scripts/jalankan_unit.sh $W -q -p no:cacheprovider $F > $L/unit.log 2>&1; grep -E "^(FAILED|ERROR) |^=+ .* in " $L/unit.log | tail -10
echo "=== SQL_KONVERSI_TERPAKAI di prod (BEGIN READ ONLY, kaos; kontrol: satuan acak = false)"
G=milkyhoop-dev-api_gateway; E=$(mktemp /root/.p1-XXXX); chmod 600 $E; printf "URL=%s\n" "$(docker exec $G printenv DATABASE_URL)" > $E
cat > $L/sql.py <<'PY'
import asyncio, os, sys
sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from app.services import satuan_dasar as SD
async def m():
    c = await asyncpg.connect(os.environ["URL"]); tx = c.transaction(readonly=True); await tx.start()
    r = await c.fetchrow("SELECT ilg.product_id, ilg.transaction_unit FROM inventory_ledger ilg WHERE ilg.tenant_id = 'kaos-biru-konveksi' AND ilg.transaction_unit IS NOT NULL LIMIT 1")
    p = r["product_id"] if r else await c.fetchval("SELECT id FROM products WHERE tenant_id = 'kaos-biru-konveksi' LIMIT 1")
    acak = await SD.konversi_terpakai(c, "kaos-biru-konveksi", p, "satuan-uji-tak-ada-zz")
    nyata = await SD.konversi_terpakai(c, "kaos-biru-konveksi", p, r["transaction_unit"]) if r else None
    print("SQL jalan: acak =", acak, "(harap False) | satuan kartu stok", r["transaction_unit"] if r else None, "=", nyata, "(harap True bila ada)")
    # KONTROL POSITIF data nyata: baris tagihan kaos bersatuan -> satuan itu WAJIB terpakai; barangnya WAJIB punya riwayat
    b = await c.fetchrow("SELECT bi.product_id, bi.unit FROM bill_items bi JOIN bills x ON x.id = bi.bill_id WHERE x.tenant_id = 'kaos-biru-konveksi' AND bi.product_id IS NOT NULL AND btrim(COALESCE(bi.unit, '')) <> '' LIMIT 1")
    pos = await SD.konversi_terpakai(c, "kaos-biru-konveksi", b["product_id"], b["unit"]) if b else None
    brg = bool(await c.fetchval(SD.SQL_BARANG_TERPAKAI, b["product_id"], "kaos-biru-konveksi")) if b else None
    kosong = bool(await c.fetchval(SD.SQL_BARANG_TERPAKAI, "00000000-0000-0000-0000-000000000000", "kaos-biru-konveksi"))
    print("KONTROL POSITIF: satuan baris tagihan", b["unit"] if b else None, "terpakai =", pos, "(harap True) | barang terpakai =", brg,
          "(harap True) | barang uuid-nol terpakai =", kosong, "(harap False)")
    print("SQL-HASIL", "LULUS" if (acak is False and pos is True and brg is True and kosong is False) else "GAGAL")
    await tx.rollback(); await c.close()
asyncio.run(m())
PY
timeout 120 docker run --rm --network milkyhoop_dev_network --env-file $E -v $W/backend:/app/backend:ro -v $L/sql.py:/s.py:ro --entrypoint python $(docker inspect $G --format '{{.Config.Image}}') -I /s.py 2>&1 | grep -vE "^(INFO|DEBUG|WARNING)" | tail -3; rm -f $E
echo "=== sabotase: tolak_ubah_konversi selalu lolos (WAJIB merah)"
P=$(mktemp -d /root/.sabp1-XXXX); mkdir -p $P/backend; rsync -a --exclude __pycache__ --exclude .pytest_cache backend/api_gateway $P/backend/; ln -s $W/backend/migrations $P/backend/migrations
S=$P/backend/api_gateway/app/services/satuan_dasar.py; sed -i 's/    tetap = {(normal(u), Decimal(str(f))) for u, f in (baru or \[\])}/    return\n    tetap = None/' $S; grep -c "    tetap = None" $S | sed 's/^/penanda_sabotase=/'
timeout 600 scripts/jalankan_unit.sh $P -q -p no:cacheprovider tests/unit/test_konversi_terkunci.py 2>&1 | grep -E "^(FAILED|ERROR) |^=+ .* in " | tail -6
rm -rf $P; echo SELESAI_SEMUA
