"""GABUNG-VENDOR (25 Okt 2026, putusan MASTER (a) tanpa migrasi, pola NetSuite/QBO = customers.merge): sumber dinonaktifkan, transaksi pindah
ke tujuan, jejak audit_logs + vendor_activities; NOL jurnal. Dulu: kolom hantu merged_into_id/deleted_at/deleted_by + tabel hantu
master_data_audit_log -> 500 tiap panggilan. Spek: penentu SAMA pratinjau/tulis, urutan idem -> kunci -> penentu -> tulis, tanpa kolom/tabel hantu."""
import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import vendors as V
from app.schemas.vendors import MergeVendorRequest

A, B = "6a263caa-0ea6-47e1-aa78-fc6575e38ff0", "0e5d7795-3a39-47bf-8e31-689766ffb05d"


def _tubuh(fn):
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_tanpa_kolom_dan_tabel_hantu():
    src = inspect.getsource(V.merge_vendors) + inspect.getsource(V._gabung_rencana) + inspect.getsource(V.preview_merge_vendors)
    for hantu in ("merged_into_id", "deleted_at", "deleted_by", "master_data_audit_log", 'UUID(ctx["tenant_id"])'):
        assert hantu not in src


def test_urutan_tulis_idem_kunci_penentu_tulis_simpan():
    p = _tubuh(V.merge_vendors)
    urut = ['idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_MERGE"', "if lama is not None",
            '_kunci_vendor(conn, ctx["tenant_id"], sumber, tujuan, saldo_awal=True)', "_gabung_rencana(conn", 'if r["blocks"]',
            "for tabel, kolom, ber_tenant, ber_nama in GABUNG_TABEL", "if pindah != r[\"cacah\"]", "UPDATE vendors SET is_active = false",
            "INSERT INTO audit_logs", 'idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_MERGE"']
    pos = [p.index(u) for u in urut]
    assert pos == sorted(pos)
    assert "journal_entries" not in p and "journal_lines" not in p  # NOL jurnal ditulis


def test_pratinjau_readonly_penentu_sama():
    p = _tubuh(V.preview_merge_vendors)
    assert "conn.transaction(readonly=True)" in p and "_gabung_rencana(conn" in p
    assert "_kunci_vendor" not in p and "UPDATE" not in p


def test_tabel_gabung_mencakup_sumber_saldo_hutang():
    t = {x[0] for x in V.GABUNG_TABEL}
    assert {"bills", "bill_payments_v2", "vendor_credits", "vendor_deposits"} <= t  # sumber atribusi compute_ap_outstanding


class _K:
    def __init__(s, vendors, ob=0, pembuka=0, tanpa_status=0):
        s.vendors, s.ob, s.pembuka, s.tanpa_status = vendors, ob, pembuka, tanpa_status

    async def fetch(s, sql, *a):
        return s.vendors

    async def fetchval(s, sql, *a):
        if "VENDOR_OPENING_BALANCE" in sql:
            return s.ob
        if "is_opening_balance" in sql:
            return s.pembuka
        if "status_v2 IS NULL" in sql:
            return s.tanpa_status
        return 0


def _v(i, aktif=True):
    from uuid import UUID
    return {"id": UUID(i), "name": f"V-{i[:4]}", "code": None, "is_active": aktif, "notes": None}


@pytest.mark.parametrize("kasus,kon,kode", [
    ("bersih", _K([_v(A), _v(B)]), []),
    ("sumber_nonaktif", _K([_v(A, False), _v(B)]), ["VENDOR_NONAKTIF"]),
    ("saldo_awal_lama", _K([_v(A), _v(B)], ob=1000), ["SALDO_AWAL_LAMA_TAK_BISA_PINDAH"]),
    ("saldo_awal_ganda", _K([_v(A), _v(B)], pembuka=2), ["SALDO_AWAL_GANDA"]),
    ("tagihan_tanpa_status", _K([_v(A), _v(B)], tanpa_status=1), ["TAGIHAN_TANPA_STATUS"]),
])
def test_penentu_blok(kasus, kon, kode):
    from uuid import UUID
    r = asyncio.run(V._gabung_rencana(kon, "t", UUID(A), UUID(B)))
    assert [b["code"] for b in r["blocks"]] == kode


def test_penentu_404_vendor_tenant_lain():
    from uuid import UUID
    with pytest.raises(HTTPException) as e:
        asyncio.run(V._gabung_rencana(_K([_v(A)]), "t", UUID(A), UUID(B)))
    assert e.value.status_code == 404


def test_sumber_sama_tujuan_400():
    with pytest.raises(HTTPException) as e:
        V._gabung_ids(MergeVendorRequest(source_vendor_id=A, target_vendor_id=A))
    assert e.value.status_code == 400


def test_ulang_berkunci_jawaban_pertama(monkeypatch):
    from app.services import idem_buat
    log = []

    class Tr:
        async def __aenter__(s): return s
        async def __aexit__(s, *a): return False

    class K:
        async def execute(s, sql, *a): log.append(sql)
        def transaction(s, **kw): return Tr()

    class CM:
        async def __aenter__(s): return K()
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def pool(): return P()
    async def mulai(*a, **k): return ("kp", "sd", {"pertama": True})
    monkeypatch.setattr(V, "get_pool", pool)
    monkeypatch.setattr(V, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(idem_buat, "kunci_dari", lambda r: "k")
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai)
    r = asyncio.run(V.merge_vendors(SimpleNamespace(headers={}), MergeVendorRequest(source_vendor_id=A, target_vendor_id=B)))
    assert r == {"pertama": True} and log == []
