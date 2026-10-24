"""AS-G15 (24 Okt 2026): penawaran -> faktur memakai pola _konversi_penawaran (kunci QUOTE SAMA dgn to-order + FOR UPDATE + faktur lama =
already_converted) + idem QUOTE_TO_INVOICE. Bukti bersamaan = harness nyata ber-commit 'TES E2E' kaos (scripts/ops/uji-nyata-as-g15.py)."""
import inspect

from app.routers import quotes as RQ


def test_kunci_sama_dengan_to_order_dan_for_update():
    s = inspect.getsource(RQ.convert_to_invoice)
    k = inspect.getsource(RQ._konversi_penawaran)
    kunci = 'f"QUOTE:{ctx[\'tenant_id\']}:{quote_id}"'
    assert kunci in s and kunci in k  # to-order x to-invoice pada satu penawaran berurutan
    i_kunci, i_baca = s.index("pg_advisory_xact_lock"), s.index("FROM quotes")
    assert i_kunci < i_baca and "FOR UPDATE" in s[i_baca:i_baca + 200]


def test_ulang_mengembalikan_faktur_yang_masih_ada_dan_menggantung_tetap_400():
    s = inspect.getsource(RQ.convert_to_invoice)
    assert 'converted_to_type") == "invoice"' in s and '"already_converted": True' in s
    assert "if faktur_lama:" in s  # faktur dihapus (menggantung, AS-G15a) -> jatuh ke cek status lama (400), bukan dibuka ulang diam-diam
    i_ulang, i_status = s.index("if faktur_lama:"), s.index('quote["status"] not in ("sent", "accepted", "viewed")')
    assert i_ulang < i_status


def test_idem_sebelum_kunci_dan_respons_tanpa_kunci_objek_lama():
    s = inspect.getsource(RQ.convert_to_invoice)
    assert s.index('idem_buat.mulai_aksi(') < s.index("pg_advisory_xact_lock") and '"QUOTE_TO_INVOICE"' in s
    assert s.count('if _kp else _hasil') == 2
