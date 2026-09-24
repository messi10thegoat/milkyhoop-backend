"""W0 Conversational Workspace (BE): kunci idempotency kiriman klien + flag fitur per tenant.

Gerbang PERILAKU ada di scripts/gate_w0.sh (DB scratch: 1 SO untuk kunci sama, 409 isi beda,
ruang kunci tenant+pengguna, gagal tak dicatat, bersamaan, features 2 tenant). Di sini: sifat
helper yang murni + penjaga penyambungan (kontrol merah = kode master tak lolos).
"""

import ast
import asyncio
from pathlib import Path

import pytest

from app.utils import idempotency as idem
from app.services import tenant_features as tf

APP = Path(idem.__file__).resolve().parents[1]


class _R:
    def __init__(self, h):
        self.headers = h


@pytest.mark.parametrize("h,harap", [
    ({}, None), ({"X-Idempotency-Key": "   "}, None), ({"X-Idempotency-Key": " k1 "}, "k1"),
    ({"Idempotency-Key": "k2"}, "k2"), ({"X-Idempotency-Key": "a", "Idempotency-Key": "b"}, "a"),
])
def test_kunci_klien(h, harap):
    assert idem.kunci_idempotensi_klien(_R(h)) == harap


def test_kunci_terlalu_panjang_ditolak_bukan_dipotong():
    idem.kunci_idempotensi_klien(_R({"X-Idempotency-Key": "x" * 200}))
    with pytest.raises(ValueError):
        idem.kunci_idempotensi_klien(_R({"X-Idempotency-Key": "x" * 201}))


def test_hash_payload_stabil_urutan_kunci_dan_peka_isi():
    a = idem.hash_payload({"b": 1, "a": [1, 2]})
    assert a == idem.hash_payload({"a": [1, 2], "b": 1})
    assert a != idem.hash_payload({"a": [2, 1], "b": 1})


class _C:
    def __init__(self, row):
        self.row, self.sql = row, []

    async def fetchrow(self, q, *a):
        return self.row

    async def execute(self, q, *a):
        self.sql.append(q)


def test_replay_isi_beda_409_isi_sama_respons_asli():
    simpan = '{"payload_hash": "h1", "response": {"success": true, "data": {"id": "x"}}}'
    c = _C({"result": simpan})
    assert asyncio.run(idem.ambil_replay_klien(c, "t", "k", "h1")) == {"success": True, "data": {"id": "x"}}
    with pytest.raises(LookupError):
        asyncio.run(idem.ambil_replay_klien(c, "t", "k", "h2"))
    assert asyncio.run(idem.ambil_replay_klien(_C(None), "t", "k", "h1")) is None


def test_simpan_menimpa_baris_kedaluwarsa_saja():
    c = _C(None)
    asyncio.run(idem.simpan_replay_klien(c, "t", "k", "SALES_ORDER_CREATE", "h", {"a": 1}))
    q = " ".join(c.sql[0].split())
    assert "ON CONFLICT (tenant_id, key) DO UPDATE" in q and "WHERE idempotency_keys.expires_at <= NOW()" in q


def test_fitur_gagal_baca_kosong_bukan_galat():
    class _Rusak:
        async def fetch(self, *a):
            raise RuntimeError('relation "tenant_features" does not exist')

    assert asyncio.run(tf.fitur_aktif(_Rusak(), "t")) == []


def _fungsi(berkas, nama):
    t = ast.parse((APP / berkas).read_text())
    hasil = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama]
    assert len(hasil) == 1
    return hasil[0]


def _panggilan(node):
    return {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None)
            for n in ast.walk(node) if isinstance(n, ast.Call)}


def test_so_create_tersambung_ke_idempotency_dan_kunci_berruang_pengguna():
    f = _fungsi("routers/sales_orders.py", "create_sales_order")
    p = _panggilan(f)
    assert {"kunci_idempotensi_klien", "ambil_replay_klien", "simpan_replay_klien", "hash_payload"} <= p
    teks = ast.unparse(f)
    assert "SO_CREATE:{ctx['user_id']}:" in teks and "pg_advisory_xact_lock" in teks
    # Lookup replay WAJIB sebelum cek nomor manual (pengulangan ber-nomor-manual bukan 409).
    assert teks.index("ambil_replay_klien") < teks.index("bersihkan_nomor_dokumen_opsional")


def test_permissions_me_membawa_features_di_kedua_jalur():
    f = _fungsi("routers/team_members.py", "get_my_permissions")
    teks = ast.unparse(f)
    assert teks.count("'features'") == 2, "jalur PolicyEngine DAN cadangan wajib membawa features"
