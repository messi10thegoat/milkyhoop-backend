"""Sabotase ND-BUAT pada SALINAN pohon (WAJIB merah). Argumen: <pohon backend/api_gateway> <S1..S10|G1..G2>. Pola harus ditemukan TEPAT 1x."""
import sys
from pathlib import Path

P = Path(sys.argv[1])
VC = "app/routers/vendor_credits.py"
S = {
    # pagar DPP dicabut -> PPN 12% (DPP 11/12) lolos
    "S1": [(VC, "        if fak and int(fak[0]) != int(fak[1]):\n", "        if False:\n")],
    # pagar DPP memblok tarif ber-faktor 1 (11%) -> blok PALSU (karakterisasi 11% merah)
    "S2": [(VC, "        if fak and int(fak[0]) != int(fak[1]):\n", "        if fak:\n")],
    # pagar diskon baris dicabut
    "S3": [(VC, "    if any(Decimal(str(i.discount_percent or 0)) > 0", "    if False and any(Decimal(str(i.discount_percent or 0)) > 0")],
    # PATCH melewati pagar
    "S4": [(VC, '                if ("items" in update_data and update_data["items"]) or update_data.get("tax_rate"):\n', "                if False:\n")],
    # penjaga ND penuh == tagihan dicabut
    "S5": [(VC, '                    if _q2(s["total_amount"]) != _q2(jumlah_tagihan):', "                    if False:")],
    # diskon dokumen tak dibagi pro-rata (ND sebagian memakai diskon tagihan PENUH)
    "S6": [(VC, "    diskon_dok = _q2(diskon_dok_tagihan * net_pilih / net_semua) if", "    diskon_dok = _q2(diskon_dok_tagihan) if")],
    # serializer JSON uang dicabut -> Decimal keluar sebagai teks: payload & sidik idem berubah untuk nominal bulat
    "S7": [("app/schemas/uang_json.py", ',\n                      PlainSerializer(_uang_json_keluar, return_type=Union[int, float], when_used="json")]', "]")],
    # tarif PPN kepala tagihan diabaikan (ND dari FPB tanpa PPN)
    "S8": [(VC, '"tax_rate": 0 if per_baris else float(bill["tax_rate"] or 0)}', '"tax_rate": 0}')],
    # nomor jurnal HPP per baris kembali "COGS-{nomor}" (tabrakan uq_je_tenant_number pada dokumen >= 2 baris barang dilacak)
    "S9": [("app/services/inventory_helpers.py", 'nomor_jurnal = f"COGS-{source_number}" if not ke else f"COGS-{source_number}-{ke + 1}"',
            'nomor_jurnal = f"COGS-{source_number}"')],
    # void ND hanya membalik jurnal HPP PERTAMA (jurnal berakhiran -2 tertinggal: stok/HPP menggantung)
    "S10": [("app/services/nota_debit.py", "AND reversed_by_id IS NULL AND reversal_of_id IS NULL ORDER BY created_at\"\"\"",
             "AND reversed_by_id IS NULL AND reversal_of_id IS NULL ORDER BY created_at LIMIT 1\"\"\"")],
    # G1 penjaga lama test_p2p3b (target dipindah ke `badan`): pratinjau tak lagi READ ONLY
    "G1": [(VC, "            tr = conn.transaction(readonly=True)  # DB menolak tulisan apa pun\n",
            "            tr = conn.transaction()  # DB menolak tulisan apa pun\n")],
    # G2 penjaga lama test_p2p3b/test_p2p7 (fixture diubah): diskon dokumen memotong (int) alih-alih 0,01 HALF_UP
    "G2": [(VC, "        overall_discount = _q2(subtotal * Decimal(str(body.discount_percent)) / 100)\n",
            "        overall_discount = Decimal(int(subtotal * Decimal(str(body.discount_percent)) / 100))\n")],
}
for berkas, lama, baru in S[sys.argv[2]]:
    f = P / berkas
    s = f.read_text(encoding="utf-8")
    if s.count(lama) != 1:
        sys.exit(f"pola sabotase {sys.argv[2]} tidak ditemukan tepat 1x di {berkas}")
    f.write_text(s.replace(lama, baru), encoding="utf-8")
print("terpasang", sys.argv[2])
