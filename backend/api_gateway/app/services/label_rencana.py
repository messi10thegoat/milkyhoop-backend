"""Label baris perubahan pada rencana pratinjau (Satuan / Gudang / Kategori): SATU bentuk {field, label, before, after, before_text, after_text}
agar FE menampilkan kalimat manusiawi dari server, bukan mengarang label dari kunci. Kosong -> '—'."""
KOSONG = "—"


def teks(v, teks_bool=None) -> str:
    if v is None or v == "":
        return KOSONG
    if isinstance(v, bool):
        return (teks_bool or ("Ya", "Tidak"))[0 if v else 1]
    return str(v)


def baris(field: str, label: str, before, after, before_text=None, after_text=None) -> dict:
    return {"field": field, "label": label, "before": before, "after": after,
            "before_text": teks(before) if before_text is None else before_text, "after_text": teks(after) if after_text is None else after_text}


# Label butir akun/pajak (SATU sumber: kategori akun, bawaan_berlaku/pratinjau Buat Barang, form bawaan)
LABEL_AKUN = {"penjualan": "Akun penjualan", "persediaan": "Akun persediaan", "hpp": "Akun HPP", "pembelian": "Akun pembelian"}
LABEL_PAJAK = {"jual": "Pajak penjualan", "beli": "Pajak pembelian"}
