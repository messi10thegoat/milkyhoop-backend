"""Draf v2 terhadap Postgres SUNGGUHAN (V469 diterapkan ke basis data sekali-pakai). Lewati bila MB2_PG_DSN tak diset
(suite unit = nol DB). Dijalankan di kontainer postgres sekali-pakai, BUKAN milkydb."""
import os
import uuid

import pytest
from fastapi import HTTPException

from app.milkybook.v2 import draf as D

DSN = os.environ.get("MB2_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="MB2_PG_DSN tak diset (butuh Postgres sekali-pakai)")
HOST_SEKALI = "mb2-pg-sekali"


def _dsn_aman(dsn: str) -> None:
    """Pagar keras SEBELUM koneksi: hanya kontainer sekali-pakai; milkydb / kontainer produksi = DITOLAK."""
    from urllib.parse import urlparse
    u = urlparse(dsn)
    if u.hostname != HOST_SEKALI or (u.path or "/").lstrip("/") in ("milkydb", "") or "milkyhoop" in (u.hostname or ""):
        raise RuntimeError(f"DITOLAK: harness draf hanya untuk {HOST_SEKALI}/postgres, bukan {u.hostname}/{u.path}")

A = {"tenant_id": "kaos", "user_id": "u1"}
B_USER = {"tenant_id": "kaos", "user_id": "u2"}
B_TENANT = {"tenant_id": "grap", "user_id": "u1"}
DRAF = {"stage": "items", "customer": {"name": "Rocky", "isNew": False}, "lines": [{"name": "Kaos", "qty": 2}]}


@pytest.fixture
async def conn():
    import asyncpg
    _dsn_aman(DSN)
    c = await asyncpg.connect(DSN)
    if await c.fetchval("SELECT current_database()") == "milkydb" or await c.fetchval("SELECT to_regclass('journal_entries')"):
        await c.close()
        raise RuntimeError("DITOLAK: basis data ini berisi tabel produksi")
    mig = open("../migrations/V469__milkybook_v2_percakapan.sql").read()
    await c.execute("DROP TABLE IF EXISTS milkybook_v2_percakapan")
    await c.execute(mig)
    await c.execute(mig)  # idempoten
    yield c
    await c.execute(open("../migrations/V469__milkybook_v2_percakapan_ROLLBACK.sql").read().replace(
        "diubah >= NOW() - INTERVAL '7 days'", "false"))
    await c.close()


async def test_alur_buat_simpan_ambil_daftar(conn):
    p = await D.buat(conn, A, D.BuatPercakapan(judul="Penawaran Rocky"))
    assert p["versi"] == 1 and p["draf"] is None and p["parkir"] == []
    s = await D.simpan(conn, A, p["id"], D.SimpanDraf(versi=1, draf=DRAF, parkir=[{"stage": "cust"}]))
    assert s["versi"] == 2 and s["draf"]["lines"][0]["qty"] == 2 and s["parkir"][0]["stage"] == "cust"
    assert (await D.ambil(conn, A, p["id"]))["draf"]["customer"]["name"] == "Rocky"
    d = await D.daftar(conn, A)
    assert [x["id"] for x in d] == [p["id"]] and d[0]["parkir"] == 1 and d[0]["draf"]["pelanggan"] == "Rocky"


async def test_pengguna_lain_dan_tenant_lain_404_dan_tak_terdaftar(conn):
    p = await D.buat(conn, A, D.BuatPercakapan())
    for lain in (B_USER, B_TENANT):
        with pytest.raises(HTTPException) as e:
            await D.ambil(conn, lain, p["id"])
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await D.simpan(conn, lain, p["id"], D.SimpanDraf(versi=1, draf=DRAF))
        assert e.value.status_code == 404
        assert await D.daftar(conn, lain) == []
    assert (await D.ambil(conn, A, p["id"]))["versi"] == 1   # tak tersentuh


async def test_versi_basi_409_dan_tak_menimpa(conn):
    p = await D.buat(conn, A, D.BuatPercakapan())
    await D.simpan(conn, A, p["id"], D.SimpanDraf(versi=1, draf=DRAF))                      # perangkat 1
    with pytest.raises(HTTPException) as e:
        await D.simpan(conn, A, p["id"], D.SimpanDraf(versi=1, draf={"stage": "cust"}))     # perangkat 2, versi lama
    assert e.value.status_code == 409 and e.value.detail["versi_kini"] == 2
    assert (await D.ambil(conn, A, p["id"]))["draf"]["stage"] == "items"


async def test_kedaluwarsa_tak_terbaca_lalu_terhapus_saat_tulis(conn):
    lama = await D.buat(conn, A, D.BuatPercakapan())
    lain = await D.buat(conn, B_USER, D.BuatPercakapan())
    await conn.execute("UPDATE milkybook_v2_percakapan SET diubah = NOW() - INTERVAL '8 days'")
    with pytest.raises(HTTPException) as e:
        await D.ambil(conn, A, lama["id"])
    assert e.value.status_code == 404 and await D.daftar(conn, A) == []
    await D.buat(conn, A, D.BuatPercakapan())                                                # tulis -> buang basi A
    ids = {str(r["id"]) for r in await conn.fetch("SELECT id FROM milkybook_v2_percakapan")}
    assert lama["id"] not in ids
    assert lain["id"] in ids                                                                 # milik orang lain tak disentuh


async def test_check_db_menolak_bentuk_salah(conn):
    import asyncpg
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("INSERT INTO milkybook_v2_percakapan (id, tenant_id, user_id, parkir) VALUES ($1,'k','u','{}'::jsonb)",
                           uuid.uuid4())
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("INSERT INTO milkybook_v2_percakapan (id, tenant_id, user_id, draf) VALUES ($1,'k','u',$2::jsonb)",
                           uuid.uuid4(), '{"x": "' + "y" * D.MAKS_BAIT + '"}')


async def test_rollback_menolak_bila_ada_draf_hidup(conn):
    import asyncpg
    await D.buat(conn, A, D.BuatPercakapan())
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(open("../migrations/V469__milkybook_v2_percakapan_ROLLBACK.sql").read())
