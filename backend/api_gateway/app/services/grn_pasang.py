"""Aturan (D) GRN -- sisi PENERIMAAN: pemasangan ke tagihan yang LEBIH DULU (putusan LANGSUNG pemilik 23 Okt, pola SAP GR/IR; kontrak disepakati
BACKEND). Fungsi MURNI (tanpa DB) supaya pembulatan & FIFO teruji tuntas; pemanggil (grn_rencana) memberi data di bawah kunci PO.

Masukan per baris PO ber-stok yang diterima:
  qty_terima, harga_terima (harga satuan PO neto diskon baris, presisi penuh)
  tertunda: tagihan TERPOSTING yang belum diterima, urut FIFO (jurnal tagihan terlama dulu):
    [{bill_item_id, qty_terbuka, nilai_terbuka (B neto sisa, 2dp), legacy: bool}]
      qty_terbuka   = qty_dasar baris tagihan - Σ qty tautan AKTIF (jenis apa pun)
      nilai_terbuka = B neto baris (tagihan_aksi.neto_tagihan_terposting) - Σ nilai_tagih tautan aktif
      legacy        = baris itu ditagih lewat jalur LAMA (ada kartu stok BILL) sebelum flag -> stok SUDAH masuk saat tagihan
Keluaran: {
  legacy: [{bill_item_id, qty}]                                -> baris GRN TERPISAH ber_stok=false + tautan kind 'legacy' (tanpa stok/jurnal)
  pasangan: [{bill_item_id, qty, nilai_terima R, nilai_tagih B, selisih B-R}] -> tautan kind 'tagih_dulu' ke baris GRN ber-stok
  qty_stok, nilai_stok (R total baris ber-stok = qty_stok x harga 2dp), qty_bebas, nilai_bebas (R bagian tak berpasangan)
  jurnal: {persediaan: R total, grni_kredit: R_bebas + Σ B, selisih: Σ (B - R)}   (Dr Persediaan + [Dr selisih] / Cr GRNI + [Cr selisih])
}
Pembulatan: nilai_terima pasangan = r2(qty x harga) KECUALI pasangan terakhir yang menghabiskan qty_terima -> R total baris - Σ R sebelumnya
(R total = r2(qty_stok x harga), sama dengan GRN tanpa pasangan); nilai_tagih = r2(nilai_terbuka x qty/qty_terbuka) KECUALI pasangan yang
menghabiskan qty_terbuka -> nilai_terbuka persis (sen tertutup di KEDUA arah; syarat MASTER (b))."""
from decimal import ROUND_HALF_UP, Decimal

NOL = Decimal("0")
SEN = Decimal("0.01")


def r2(v: Decimal) -> Decimal:
    return v.quantize(SEN, rounding=ROUND_HALF_UP)


def pasangkan(qty_terima: Decimal, harga_terima: Decimal, tertunda: list) -> dict:
    sisa = qty_terima
    legacy, mentah = [], []
    for t in tertunda:
        if sisa <= NOL:
            break
        q = min(sisa, t["qty_terbuka"])
        if q <= NOL:
            continue
        if t.get("legacy"):
            legacy.append({"bill_item_id": t["bill_item_id"], "qty": q})
        else:
            b = t["nilai_terbuka"] if q == t["qty_terbuka"] else r2(t["nilai_terbuka"] * q / t["qty_terbuka"])
            mentah.append({"bill_item_id": t["bill_item_id"], "qty": q, "nilai_tagih": b})
        sisa -= q
    qty_legacy = sum((x["qty"] for x in legacy), NOL)
    qty_stok = qty_terima - qty_legacy
    nilai_stok = r2(qty_stok * harga_terima)
    pasangan, r_dibagi = [], NOL
    qty_pas = sum((x["qty"] for x in mentah), NOL)
    for k, x in enumerate(mentah):
        habis = (k == len(mentah) - 1) and qty_pas == qty_stok
        r = (nilai_stok - r_dibagi) if habis else r2(x["qty"] * harga_terima)
        r_dibagi += r
        pasangan.append({**x, "nilai_terima": r, "selisih": x["nilai_tagih"] - r})
    nilai_bebas = nilai_stok - r_dibagi
    b_total = sum((x["nilai_tagih"] for x in pasangan), NOL)
    selisih = sum((x["selisih"] for x in pasangan), NOL)
    return {"legacy": legacy, "pasangan": pasangan, "qty_stok": qty_stok, "nilai_stok": nilai_stok,
            "qty_bebas": qty_stok - qty_pas, "nilai_bebas": nilai_bebas,
            "jurnal": {"persediaan": nilai_stok, "grni_kredit": nilai_bebas + b_total, "selisih": selisih}}
