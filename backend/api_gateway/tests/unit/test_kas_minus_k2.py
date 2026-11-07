"""K2 kas-negatif (7 Nov 2026; putusan pemilik "kas minus DITOLAK -> hitung fisik", lingkup MASTER): kasbon karyawan, pembayaran gaji/
PPh21/BPJS (kaki kas), aktivasi aktiva tetap, dan posting jurnal MANUAL memakai aturan SATU kasbank_tulis (K1/B8). Penjaga dipanggil
SESUDAH kunci dokumen dan SEBELUM jurnal ditulis (kunci kas per CoA diambil di dalam penjaga sebelum saldo dibaca); pratinjau kasbon
mengumpulkan blok TANPA kunci; void/pembalikan TIDAK dijaga (putusan MASTER 6 Nov). Perilaku dua sisi nyata: scripts/ops/uji-salinan-k2.*"""
import inspect
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import employee_advances as EA  # noqa: E402
from app.routers import fixed_assets as FA  # noqa: E402
from app.routers import journals as J  # noqa: E402
from app.routers import payroll_payments as PP  # noqa: E402


def _s(fn):
    return " ".join(inspect.getsource(fn).split())


def test_kasbon_rencana_menjaga_kas_dan_tulis_mengunci():
    r = _s(EA._rencana_kasbon)
    assert 'KT.tolak_kas_minus_coa(conn, tid, src["id"], principal, KT.Blok(True), kunci=not pratinjau)' in r
    assert "pratinjau: bool = False" in r
    g = _s(EA.grant_advance)
    assert "await _rencana_kasbon(conn, ctx, body)" in g and g.index("_rencana_kasbon(") < g.index("_tulis_kasbon(")
    assert g.index('if b["code"] == "KAS_MINUS":') < g.index('raise GalatBerkode(b["status"], b["code"], b["message"])') < g.index("_tulis_kasbon(")
    assert "await _rencana_kasbon(conn, ctx, body, pratinjau=True)" in _s(EA.preview_grant_advance)


def test_kasbon_void_tidak_dijaga():
    assert "tolak_kas_minus" not in _s(EA._rencana_batal_kasbon) + _s(EA.void_advance)


def test_gaji_kaki_kas_dijaga_sebelum_jurnal():
    p = _s(PP.post_payment)
    jaga = p.index('await KT.tolak_kas_minus_baris(conn, ctx["tenant_id"], [(c, 0, a) for c, a, _ in credit_accounts])')
    assert p.index('f"PAYROLL_PAYMENT:{payment_id}"') < jaga < p.index("INSERT INTO journal_entries")
    assert p.index('credit_accounts.append((bank_coa, total_debit') < jaga   # SEMUA kaki kredit (gaji transfer+tunai, PPh, BPJS) sudah terkumpul
    assert "tolak_kas_minus" not in _s(PP.void_payment)


def test_aktiva_tetap_dijaga_sebelum_jurnal():
    a = _s(FA.activate_fixed_asset)
    jaga = a.index('await KT.tolak_kas_minus_coa(conn, ctx["tenant_id"], data.payment_account_id, Decimal(str(fa["purchase_price"])))')
    assert a.index('f"FIXED_ASSET:{asset_id}"') < jaga < a.index("INSERT INTO journal_entries")


def test_jurnal_manual_buat_posting_dan_posting_draf_dijaga_pembalikan_tidak():
    c = _s(J.create_journal)
    jaga = c.index('await KT.tolak_kas_minus_baris(conn, ctx["tenant_id"], [(UUID(ln.account_id), ln.debit, ln.credit) for ln in body.lines])')
    assert c.index('f"MANUAL_CREATE:{ctx[\'tenant_id\']}"') < jaga < c.index("UPDATE journal_entries SET status = 'POSTED'")
    assert c.index('if initial_status == "POSTED":') < jaga   # draf tak dijaga saat dibuat; dijaga saat diposting
    p = _s(J.post_journal)
    jp = p.index("await KT.tolak_kas_minus_baris(")
    assert p.index('f"MANUAL_POST:{str(journal_id)}"') < jp < p.index("SET status = 'POSTED', updated_at = NOW()")
    assert 'if journal["source_type"] == "MANUAL":' in p
    assert "tolak_kas_minus" not in _s(J.reverse_journal)
