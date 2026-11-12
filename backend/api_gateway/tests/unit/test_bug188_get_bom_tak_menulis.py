"""BUG-188: GET /api/bom/{id} dan /cost-breakdown TIDAK menulis (dulu memanggil calculate_bom_cost = UPDATE bill_of_materials + updated_at, juga untuk BOM AKTIF).
Angka TERSIMPAN dipakai apa adanya; penyegaran hanya lewat jalur tulis (buat/ubah/duplikat/komponen/operasi/recalculate)."""
import inspect
import re

import app.routers.bom as B


def _kode(fn) -> str:
    """Sumber fungsi tanpa komentar (komentar boleh menyebut nama fungsi)."""
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in inspect.getsource(fn).splitlines())


def test_get_bom_dan_rincian_biaya_tak_memanggil_fungsi_tulis():
    for fn in (B.get_bom, B.get_cost_breakdown):
        s = _kode(fn)
        assert "calculate_bom_cost" not in s and "UPDATE" not in s.upper().replace("UPDATED_AT", ""), fn.__name__


def test_jalur_tulis_tetap_menyegarkan_biaya():
    for fn in (B.create_bom, B.update_bom, B.duplicate_bom, B.add_component, B.add_operation, B.recalculate_cost):
        assert "calculate_bom_cost" in inspect.getsource(fn), fn.__name__
    assert "calculate_bom_cost" in inspect.getsource(B.recalculate_cost_preview)       # pratinjau ROLLBACK tetap memakai jalur tulis yang sama


def test_semua_pemanggil_fungsi_adalah_penulis_bukan_pembaca():
    src = inspect.getsource(B)
    import re
    for m in re.finditer(r'@router\.(get|head)\([^)]*\)\s*\nasync def (\w+)', src):
        assert "calculate_bom_cost" not in _kode(getattr(B, m.group(2))), m.group(2)
