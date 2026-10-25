"""SA-KOREKSI (25 Okt 2026, aturan DIKONFIRMASI LANGSUNG pemilik): (1) void tagihan PEMBUKA saldo awal yang dipotong uang muka DITOLAK
(SALDO_AWAL_ADA_UANG_MUKA; tagihan biasa tetap dilepas otomatis, putusan 22 Okt); (2) pintu generik balik saldo awal vendor CARA LAMA
(jurnal VENDOR_OPENING_BALANCE): pratinjau READ ONLY + tulis idem + kunci, jurnal pembalik source REVERSAL (whitelist guard AR/AP), jurnal lama
tetap ada (reversed_by_id)."""
import ast
import asyncio
import inspect
import textwrap

import pytest

from app.routers import vendors as V
from app.services import tagihan_aksi as TA


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_balik_urutan_idem_kunci_penentu_tulis_simpan():
    p = _tubuh(V.reverse_legacy_opening_balance)
    urut = ['idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_OB_LAMA_BALIK"', "if lama is not None",
            '_kunci_vendor(conn, ctx["tenant_id"], vendor_id, saldo_awal=True)', "_rencana_balik_saldo_awal_lama(conn", 'if r["blocks"]',
            "INSERT INTO journal_entries", "'REVERSAL'", "'DRAFT'", "SET status = 'POSTED'", "SET reversed_by_id", "INSERT INTO audit_logs",
            'idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_OB_LAMA_BALIK"']
    pos = [p.index(u) for u in urut]
    assert pos == sorted(pos)
    assert "DELETE" not in p and "'VENDOR_OPENING_BALANCE'" not in p.split("INSERT INTO journal_entries")[1].split("RETURNING")[0]


def test_baris_pembalik_ditukar():
    p = _tubuh(V.reverse_legacy_opening_balance)
    assert 'rid, b["account_id"], b["credit"], b["debit"]' in p


def test_pratinjau_readonly_tanpa_kunci_penentu_sama():
    p = _tubuh(V.preview_reverse_legacy_opening_balance)
    assert "conn.transaction(readonly=True)" in p and "_rencana_balik_saldo_awal_lama(conn" in p and "_kunci_vendor" not in p


class _K:
    def __init__(s, pembuka, um):
        s.pembuka, s.um = pembuka, um

    async def fetch(s, sql, *a):
        if "vendor_deposit_applications" in sql:
            return [{"application_id": 1, "deposit_id": 2, "amount": 1000, "deposit_number": "VD-1"}] if s.um else []
        return []

    async def fetchval(s, sql, *a):
        if "is_opening_balance" in sql:
            return s.pembuka
        return None


BILL = {"id": "b", "invoice_number": "PB-1", "status": "posted", "status_v2": "posted"}


@pytest.mark.parametrize("pembuka,um,kode", [(True, True, ["SALDO_AWAL_ADA_UANG_MUKA"]), (True, False, []), (False, True, []), (False, False, [])])
def test_void_pembuka_ber_um_ditolak_tagihan_biasa_tidak(pembuka, um, kode):
    b = asyncio.run(TA.blok_void(_K(pembuka, um), "t", BILL))
    assert [x["code"] for x in b] == kode
    if kode:
        assert "VD-1" in b[0]["message"] and "uang muka" in b[0]["message"]
