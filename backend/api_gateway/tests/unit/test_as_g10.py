"""AS-G10 (24 Okt 2026) UM Pelanggan: idem PATCH/hapus/posting (DEPOSIT_UPDATE/DELETE/POST) + PATCH berkunci SAMA dengan posting
(DEPOSIT_POST + FOR UPDATE, UPDATE bersyarat draf) + pratinjau ubah READ ONLY dengan penentu SAMA (_ubah_dp)."""
import ast
import asyncio
import inspect
import textwrap

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as CD
from app.schemas.customer_deposits import UpdateCustomerDepositRequest


def _tubuh(fn):
    """Sumber pernyataan TANPA docstring/komentar antar-pernyataan -- urutan diukur dari KODE (pelajaran AS-GP4)."""
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


KUNCI = 'f"DEPOSIT_POST:{deposit_id}"'


def test_patch_idem_lalu_kunci_posting_lalu_ifmatch_lalu_penentu_bersama():
    p = _tubuh(CD.update_customer_deposit)
    assert '"DEPOSIT_UPDATE"' in p
    assert p.index("mulai_aksi(") < p.index(KUNCI) < p.index("assert_if_match_row(request") < p.index("_ubah_dp(conn, ctx, deposit_id, body, tulis=True)")


def test_ubah_baca_for_update_dan_tulis_bersyarat_draf_sesudah_semua_pemeriksaan():
    u = _tubuh(CD._ubah_dp)
    assert '("FOR UPDATE" if tulis else "")' in u  # PATCH mengunci baris; pratinjau (READ ONLY) tidak
    assert "AND status = 'draft'" in u and 'if n != "UPDATE 1":' in u
    # pratinjau berhenti SESUDAH pemeriksaan terakhir (plafon) dan SEBELUM menulis
    assert u.index("pelanggan_kanonik_tenant(") < u.index("assert_deposit_within_order_total(") < u.index("if not tulis:") \
        < u.index("UPDATE customer_deposits")


def test_pratinjau_ubah_read_only_tanpa_kunci_penentu_sama():
    pv = _tubuh(CD.preview_edit_customer_deposit)
    assert "transaction(readonly=True)" in pv and "pg_advisory" not in pv and "FOR UPDATE" not in pv
    assert "_ubah_dp(conn, ctx, deposit_id, body, tulis=False)" in pv


def test_posting_idem_lalu_kunci_dan_baca_for_update():
    p = _tubuh(CD.post_customer_deposit)
    assert '"DEPOSIT_POST", deposit_id' in p
    assert p.index("mulai_aksi(") < p.index(KUNCI) < p.index("_post_deposit(conn, ctx, deposit_id)")
    pd = _tubuh(CD._post_deposit)
    assert pd.index("FOR UPDATE") < pd.index("UPDATE customer_deposits")
    # pemanggil lain _post_deposit (buat + auto_post) memegang kunci yang SAMA sebelum memanggil
    c = _tubuh(CD.create_customer_deposit)
    assert c.index('f"DEPOSIT_POST:{dep_id}"') < c.index("_post_deposit(conn, ctx, dep_id)")


def test_hapus_idem_sebelum_kunci():
    d = _tubuh(CD.delete_customer_deposit)
    assert '"DEPOSIT_DELETE"' in d and d.index("mulai_aksi(") < d.index(KUNCI)


def test_izin_pratinjau_ubah_sama_dengan_patch():
    from app.middleware.permission_middleware import izin_untuk_jalur
    u = "/api/customer-deposits/6a263caa-0ea6-47e1-aa78-fc6575e38ff0"
    assert izin_untuk_jalur(u + "/edit/preview", "POST") == izin_untuk_jalur(u, "PATCH") == ("customer_deposit", "U")
    assert "/{deposit_id}/edit/preview" in [r.path for r in CD.router.routes]


class _K:
    """Koneksi palsu: baris uang muka + catat SQL. Tulis apa pun di jalur pratinjau = gagal."""

    def __init__(self, status="draft", tulis_dilarang=False):
        self.status, self.tulis_dilarang, self.sql = status, tulis_dilarang, []

    async def fetchrow(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        if "SELECT id, status, sales_order_id, proforma_id" in sql:
            return {"id": a[0], "status": self.status, "sales_order_id": None, "proforma_id": None}
        if sql.startswith("SELECT deposit_number, "):
            from collections import defaultdict
            return defaultdict(lambda: None, deposit_number="DP-G10")
        return None

    async def execute(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        assert not self.tulis_dilarang, f"pratinjau MENULIS: {sql[:60]}"
        return "UPDATE 1"

    async def fetchval(self, sql, *a):
        return None


CTX = {"tenant_id": "kaos-biru-konveksi", "user_id": "u-1"}
DEP = "6a263caa-0ea6-47e1-aa78-fc6575e38ff0"


def test_pratinjau_berhenti_sebelum_tulis_dan_tanpa_for_update():
    k = _K(tulis_dilarang=True)
    r = asyncio.run(CD._ubah_dp(k, CTX, DEP, UpdateCustomerDepositRequest(notes="TES E2E"), tulis=False))
    assert r == {"changes": {"notes": "TES E2E"}}
    assert not any("FOR UPDATE" in s for s in k.sql) and not any(s.startswith("UPDATE") for s in k.sql)


def test_tulis_for_update_dan_update_bersyarat_draf():
    k = _K()
    r = asyncio.run(CD._ubah_dp(k, CTX, DEP, UpdateCustomerDepositRequest(notes="TES E2E"), tulis=True))
    assert r["success"] is True
    assert any("FOR UPDATE" in s for s in k.sql if "FROM customer_deposits" in s)
    upd = [s for s in k.sql if s.startswith("UPDATE customer_deposits")]
    assert len(upd) == 1 and "AND status = 'draft'" in upd[0]


@pytest.mark.parametrize("tulis", [False, True])
def test_bukan_draf_ditolak_pesan_sama_di_pratinjau_dan_patch(tulis):
    with pytest.raises(HTTPException) as e:
        asyncio.run(CD._ubah_dp(_K(status="posted", tulis_dilarang=True), CTX, DEP, UpdateCustomerDepositRequest(notes="x"), tulis=tulis))
    assert e.value.status_code == 400 and e.value.detail == "Hanya uang muka berstatus Draf yang bisa diubah."


def test_tubuh_tanpa_docstring_kontrol():
    sumber = 'async def f():\n    """FOR UPDATE di docstring"""\n    # FOR UPDATE di komentar\n    x = 1\n    return "FOR UPDATE"\n'
    ns = {}
    exec(compile(sumber, "<kontrol-g10>", "exec"), ns)
    import linecache
    linecache.cache["<kontrol-g10>"] = (len(sumber), None, sumber.splitlines(True), "<kontrol-g10>")
    t = _tubuh(ns["f"])
    assert t.count("FOR UPDATE") == 1 and t.index("x = 1") < t.index("FOR UPDATE")
