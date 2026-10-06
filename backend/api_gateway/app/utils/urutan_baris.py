"""Urutan baris dokumen (6 Okt 2026, MASTER: urutan baris di PDF beda dari isian = cacat di dokumen pelanggan).

Skema item Penawaran/SO memberi sort_order BAWAAN 0 -> klien yang tak mengirimnya menghasilkan semua baris 0 dan
urutan acak di PDF (terukur kaos: 1 penawaran + 2 SO). Aturan: sort_order yang DIKIRIM klien dihormati; yang tidak
dikirim = indeks array (urutan isian). Pembaca WAJIB ORDER BY sort_order, id (penentu stabil; data lama tanpa backfill).
"""


def urutan_isian(items) -> list:
    """[sort_order efektif per baris] -- model Pydantic: dikirim (model_fields_set) -> nilainya, selain itu indeks."""
    return [it.sort_order if "sort_order" in getattr(it, "model_fields_set", set()) and it.sort_order is not None
            else i for i, it in enumerate(items)]
