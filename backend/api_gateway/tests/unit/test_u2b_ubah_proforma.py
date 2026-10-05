"""U2b (WORKSPACE/MASTER 5 Okt 2026): pratinjau ubah draf proforma + idempotensi PATCH.
POST /proformas/{id}/update/preview = penentu _rencana_ubah (urutan & pesan = PATCH) + penulis _tulis_ubah YANG SAMA
di savepoint lalu ROLLBACK. PATCH ber-X-Idempotency-Key (replay SEBELUM penentu). Perilaku nyata = harness kaos
tx-ROLLBACK (/root/uji_u2b.py)."""
import inspect
from pathlib import Path

from app.routers import proformas as PF

MW = Path(PF.__file__).parents[1] / "middleware" / "permission_middleware.py"


def _rata(f):
    return " ".join(inspect.getsource(f).split())


def test_rute_dan_izin():
    nama = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in PF.router.routes}
    assert nama[("/{proforma_id}/update/preview", ("POST",))] == "preview_update_proforma"
    t = MW.read_text()
    assert t.index('(r"^/api/proformas/[^/]+/update/preview$", ["POST"], "proforma", "U")') < \
        t.index('(r"^/api/proformas", ["POST"], "proforma", "C")')  # pola khusus SEBELUM awalan POST=C


def test_patch_dan_pratinjau_memakai_penentu_dan_penulis_yang_sama():
    p = _rata(PF.update_proforma)
    assert "r = await _rencana_ubah(conn, ctx, cur, body)" in p and "_angkat_blok_pertama(r)" in p
    assert "row = await _tulis_ubah(conn, ctx, cur, body, r)" in p
    assert p.index('_idem_aksi(conn, ctx, request, "UPDATE"') < p.index("_kunci_proforma(")  # replay sebelum penentu
    pr = _rata(PF._pratinjau)
    assert "r = await _rencana_ubah(conn, ctx, cur, body)" in pr and "await _tulis_ubah(conn, ctx, cur, body, r)" in pr
    assert 'data["payload"] = _payload_ubah(body, r)' in pr


def test_penentu_urutan_dan_teks_indonesia():
    s = inspect.getsource(PF._rencana_ubah)
    urut = [s.index(k) for k in ("PROFORMA_NOT_DRAFT", "PROFORMA_PURPOSE_INVALID", "PROFORMA_PERCENT_INVALID",
                                 "PROFORMA_AMOUNT_INVALID", "PROFORMA_EXCEEDS_BILLABLE")]
    assert urut == sorted(urut)
    assert "harus salah satu dari" not in s and "lebih besar dari 0" not in s


def test_penulis_menjaga_status_draf():
    assert "WHERE id = $1 AND tenant_id = $2 AND status = 'draft'" in inspect.getsource(PF._tulis_ubah)
