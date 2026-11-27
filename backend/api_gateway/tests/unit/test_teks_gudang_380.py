"""BUG-380: pesan sukses endpoint gudang/batch/serial/bin/status barang berbahasa Indonesia."""
from app.schemas import warehouses as W, item_batches as B  # serial: skema dihapus S4 (respons dict, teks server)


def _msgs(*kelas):
    return [k.model_fields["message"].default for k in kelas]


def test_default_message_tanpa_inggris():
    semua = [n for m in (W, B) for n in vars(m).values()
             if isinstance(n, type) and "message" in getattr(n, "model_fields", {})
             and isinstance(n.model_fields["message"].default, str)]
    teks = _msgs(*semua)
    assert teks and not [t for t in teks if "successfully" in t.lower()], teks


def test_gudang_diperbarui():
    assert W.UpdateWarehouseResponse.model_fields["message"].default == "Gudang diperbarui"
