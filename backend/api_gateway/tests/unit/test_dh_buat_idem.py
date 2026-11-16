"""BUG-250 (15 Nov 2026): POST /price-lists dengan X-Idempotency-Key membalas 500 -- idempotency_keys.result_id bertipe uuid, sedangkan DH_BUAT
meneruskan doc_id "baru". result_id harus UUID daftar yang baru dibuat; kunci idempotensi tetap boleh memakai "baru"."""
import asyncio
import uuid

import pytest
from starlette.requests import Request

from app.routers import price_lists as PL
from app.schemas.price_lists import CreatePriceListRequest
from app.services import harga_aksi as HA

NEW = uuid.uuid4()


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False


class Konn:
    def __init__(self):
        self.sql = []

    def transaction(self):
        return Tx()

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))

    async def fetchrow(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return None

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return NEW if sql.lstrip().upper().startswith("INSERT INTO PRICE_LISTS") else None


class Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class C:
            async def __aenter__(s):
                return k

            async def __aexit__(s, *e):
                return False
        return C()


def _req(kunci=None):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/api/price-lists", "headers": h})
    r.state.user = {"tenant_id": "t1", "user_id": str(uuid.uuid4())}
    return r


def _jalan(monkeypatch, kunci):
    k = Konn()

    async def pool():
        return Pool(k)

    async def siapkan(conn, tid, b):
        return {"code": "GRS", "name": "Grosir"}
    monkeypatch.setattr(PL, "get_pool", pool)
    monkeypatch.setattr(PL, "siapkan_buat_daftar", siapkan)

    async def snap_daftar(conn, tid, pl_id, batas=200):          # BUG-252: penulis riwayat diuji di test_dh_riwayat_252 (koneksi tiruan tanpa fetch)
        return []

    async def catat(*a, **kw):
        return None
    monkeypatch.setattr(PL.HR, "snapshot_daftar", snap_daftar)
    monkeypatch.setattr(PL.HR, "catat", catat)
    r = asyncio.run(PL.create_price_list(_req(kunci), CreatePriceListRequest(code="GRS", name="Grosir")))
    return r, k


def _simpan(k):
    return [a for s, a in k.sql if s.startswith("INSERT INTO idempotency_keys")]


def test_buat_dengan_kunci_menyimpan_result_id_uuid_daftar_baru(monkeypatch):
    r, k = _jalan(monkeypatch, "kunci-uji-1")
    assert r["success"] and r["data"]["id"] == str(NEW)
    ins = _simpan(k)
    assert len(ins) == 1
    result_id = ins[0][4]                       # kolom ke-5 INSERT = result_id (uuid)
    assert isinstance(result_id, uuid.UUID) and result_id == NEW and "baru" in ins[0][0 if False else 0]  # kunci penuh memuat "baru"


def test_buat_tanpa_kunci_tak_menulis_idempotensi(monkeypatch):
    r, k = _jalan(monkeypatch, None)
    assert r["success"] and not _simpan(k)


def test_idem_aksi_doc_id_bukan_uuid_tanpa_data_id_menyimpan_none(monkeypatch):
    k = Konn()
    ctx = {"tenant_id": "t1", "user_id": "u1"}

    async def kerja():
        return {"success": True}
    asyncio.run(HA.idem_aksi(k, ctx, _req("k2"), "DH_BUAT", "baru", {"a": 1}, kerja))
    assert _simpan(k)[0][4] is None


def test_idem_aksi_doc_id_uuid_tetap_dipakai_sebagai_result_id():
    k = Konn()
    ctx = {"tenant_id": "t1", "user_id": "u1"}
    did = uuid.uuid4()

    async def kerja():
        return {"success": True}
    asyncio.run(HA.idem_aksi(k, ctx, _req("k3"), "DH_UBAH", did, {"a": 1}, kerja))
    assert _simpan(k)[0][4] == did


# ---- jaring umum (BUG-250, permintaan MASTER): result_id idempotency_keys bertipe uuid -- tak boleh ada penyimpan yang mengirim teks non-UUID
@pytest.mark.parametrize("doc_id", ["baru", "new", "-", 5, None])
def test_idem_aksi_tak_pernah_menyimpan_result_id_non_uuid(doc_id):
    k = Konn()
    ctx = {"tenant_id": "t1", "user_id": "u1"}

    async def kerja():
        return {"success": True, "data": {"id": "bukan-uuid"}}
    asyncio.run(HA.idem_aksi(k, ctx, _req("kj"), "X", doc_id, {"a": 1}, kerja))
    rid = _simpan(k)[0][4]
    assert rid is None or isinstance(rid, uuid.UUID)


def test_tak_ada_penyimpan_idempotensi_di_kode_dengan_result_id_teks_literal():
    import ast
    import pathlib
    app = pathlib.Path(__file__).resolve().parents[2] / "app"
    # (nama fungsi -> indeks posisi result_id): idem_buat.simpan(conn, ctx, kp, sd, source_type, resp, result_id); simpan_replay_klien(..., respons, result_id)
    posisi = {"simpan": 6, "simpan_replay_klien": 6}
    salah = []
    for p in app.rglob("*.py"):
        try:
            pohon = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for n in ast.walk(pohon):
            if not isinstance(n, ast.Call):
                continue
            nama = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            if nama not in posisi:
                continue
            args = list(n.args)
            kandidat = [args[posisi[nama]]] if len(args) > posisi[nama] else []
            kandidat += [kw.value for kw in n.keywords if kw.arg == "result_id"]
            if any(isinstance(c, ast.Constant) and isinstance(c.value, str) for c in kandidat):
                salah.append(f"{p.relative_to(app)}:{n.lineno}")
    assert not salah, salah
