"""Law 14 Nota Debit (23 Okt 2026, putusan pemilik LANGSUNG; kembar NK CN_*): post/apply/unapply/receive-refund/void membaca X-Idempotency-Key
klien lewat idem_buat.mulai_aksi SEBELUM pekerjaan apa pun, dan SETIAP jawaban sukses lewat idem_buat.simpan (replay = respons pertama)."""
import ast
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "app/routers/vendor_credits.py").read_text()
AKSI = {"post_vendor_credit": "VC_POST", "apply_vendor_credit": "VC_APPLY", "unapply_vendor_credit": "VC_UNAPPLY",
        "receive_refund": "VC_REFUND", "void_vendor_credit": "VC_VOID"}


def _fungsi(nama: str) -> str:
    t = ast.parse(SRC)
    for n in t.body:
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nama:
            return ast.get_source_segment(SRC, n)
    raise AssertionError(nama)


def test_tiap_aksi_mulai_aksi_dulu_lalu_simpan():
    for nama, pref in AKSI.items():
        f = _fungsi(nama)
        assert "response: Response = None" in f, nama
        i_mulai = f.index(f'_ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "{pref}", vendor_credit_id')
        assert f.index("async with conn.transaction():") < i_mulai, nama
        # mulai_aksi sebelum penentu / kunci domain / tulisan apa pun
        for awal in ("NDR.rencana_", "ND.terapkan_nota_debit(", "ND.lepas_nota_debit(", "pg_advisory_xact_lock", "INSERT INTO", "DELETE FROM"):
            if awal in f:
                assert i_mulai < f.index(awal), (nama, awal)
        assert "if _lama is not None:\n" in f and "return _lama" in f, nama
        # setiap return sukses di dalam transaksi lewat simpan(prefix yang sama)
        n_simpan = f.count(f'_ib.simpan(conn, ctx, _kp, _sd, "{pref}",')
        n_return_sukses = len(re.findall(r'return \{\s*"success": True|return \{\n\s*"success": True', f))
        assert n_simpan >= 1 and n_return_sukses == 0, (nama, n_simpan, n_return_sukses)


def test_isi_sidik_badan_aksi():
    for nama in ("apply_vendor_credit", "unapply_vendor_credit", "receive_refund", "void_vendor_credit"):
        assert 'lambda: body.model_dump(mode="json"), response)' in _fungsi(nama), nama
    assert '"VC_POST", vendor_credit_id,\n' in _fungsi("post_vendor_credit") and "{}, response)" in _fungsi("post_vendor_credit")
