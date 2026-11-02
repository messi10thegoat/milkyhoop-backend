"""SQL pilihan Dari/Akun catat biaya v2 terhadap Postgres SUNGGUHAN (tabel TEMP berkolom sama dengan produksi). Lewati bila
MB2_PG_DSN tak diset (suite unit = nol DB). Dijalankan di kontainer postgres sekali-pakai, BUKAN milkydb."""
import os
import uuid
from datetime import date, timedelta

import pytest

from app.milkybook.v2 import biaya as B

DSN = os.environ.get("MB2_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="MB2_PG_DSN tak diset (butuh Postgres sekali-pakai)")
HOST_SEKALI = "mb2-pg-sekali"
HARI = date(2026, 11, 2)


def _dsn_aman(dsn: str) -> None:
    """Pagar keras SEBELUM koneksi: hanya kontainer sekali-pakai; milkydb / kontainer produksi = DITOLAK."""
    from urllib.parse import urlparse
    u = urlparse(dsn)
    if u.hostname != HOST_SEKALI or (u.path or "/").lstrip("/") in ("milkydb", "") or "milkyhoop" in (u.hostname or ""):
        raise RuntimeError(f"DITOLAK: harness biaya hanya untuk {HOST_SEKALI}/postgres, bukan {u.hostname}/{u.path}")


SKEMA = """
CREATE TEMP TABLE bank_accounts (id uuid PRIMARY KEY, tenant_id text NOT NULL, account_name text, account_type text,
  is_active boolean, coa_id uuid);
CREATE TEMP TABLE chart_of_accounts (id uuid PRIMARY KEY, tenant_id text NOT NULL, name text, account_type text, is_header boolean);
CREATE TEMP TABLE expenses (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id text NOT NULL, paid_through_id uuid,
  account_id uuid, notes text, status text, expense_date date);
CREATE TEMP TABLE account_roles (tenant_id text NOT NULL, role_key text NOT NULL, account_id uuid NOT NULL,
  UNIQUE (tenant_id, role_key));
"""


def _id():
    return uuid.uuid4()


@pytest.fixture
async def conn():
    import asyncpg
    _dsn_aman(DSN)
    c = await asyncpg.connect(DSN)
    if await c.fetchval("SELECT current_database()") == "milkydb" or await c.fetchval("SELECT to_regclass('journal_entries')"):
        await c.close()
        raise RuntimeError("DITOLAK: basis data ini berisi tabel produksi")
    await c.execute(SKEMA)
    yield c
    await c.close()  # tabel TEMP hilang bersama sesi


async def _isi(c):
    k = {n: _id() for n in ("kas_op", "toko", "bca", "kas_lain_tenant", "nonaktif")}
    k["coa_bca"] = _id()
    a = {n: _id() for n in ("transport", "lain", "header", "pendapatan", "lain_tenant")}
    await c.executemany("INSERT INTO bank_accounts VALUES ($1, $2, $3, $4, $5, $6)", [
        (k["kas_op"], "kaos", "Kas Operasional", "petty_cash", True, _id()),
        (k["toko"], "kaos", "Toko Manado", "petty_cash", True, _id()),
        (k["bca"], "kaos", "BCA", "bank", True, k["coa_bca"]),
        (k["nonaktif"], "kaos", "Kas Lama", "petty_cash", False, _id()),
        (k["kas_lain_tenant"], "grap", "Kas Grap", "petty_cash", True, _id())])
    await c.executemany("INSERT INTO chart_of_accounts VALUES ($1, $2, $3, $4, $5)", [
        (a["transport"], "kaos", "Beban Transportasi", "EXPENSE", False),
        (a["lain"], "kaos", "Beban Lain-lain", "EXPENSE", False),
        (a["header"], "kaos", "Beban Operasional", "EXPENSE", True),
        (a["pendapatan"], "kaos", "Penjualan", "REVENUE", False),
        (a["lain_tenant"], "grap", "Beban Bensin Grap", "EXPENSE", False)])
    return k, a


async def _biaya(c, tenant, kas, akun, notes, status="posted", umur=1):
    await c.execute("INSERT INTO expenses (tenant_id, paid_through_id, account_id, notes, status, expense_date) "
                    "VALUES ($1, $2, $3, $4, $5, CURRENT_DATE - $6::int)", tenant, kas, akun, notes, status, umur)


async def test_riwayat_menentukan_dari_dan_akun_serupa(conn):
    k, a = await _isi(conn)
    for _ in range(3):
        await _biaya(conn, "kaos", k["kas_op"], a["transport"], "Bensin motor")
    await _biaya(conn, "kaos", k["toko"], a["lain"], "parkir")
    for _ in range(9):  # tenant lain + void + lewat 90 hari TIDAK dihitung
        await _biaya(conn, "grap", k["kas_lain_tenant"], a["lain_tenant"], "bensin")
    await _biaya(conn, "kaos", k["toko"], a["lain"], "bensin", status="void")
    await _biaya(conn, "kaos", k["toko"], a["lain"], "bensin", umur=B.HARI_RIWAYAT + 5)
    p = await B.pratinjau(conn, "kaos", "bensin", 50000, HARI)
    assert p["dari"] == {"id": str(k["kas_op"]), "nama": "Kas Operasional"}
    assert p["akun"] == {"id": str(a["transport"]), "nama": "Beban Transportasi"} and p["siap"]
    assert [x["nama"] for x in p["pilihan_dari"]] == ["Kas Operasional", "Toko Manado", "BCA"]  # nonaktif & tenant lain hilang
    assert {x["nama"] for x in p["pilihan_akun"]} == {"Beban Transportasi", "Beban Lain-lain"}  # header & pendapatan hilang


async def test_tanpa_riwayat_tanpa_peran_dua_kas_dan_akun_kosong(conn):
    await _isi(conn)
    p = await B.pratinjau(conn, "kaos", "bensin", 50000, HARI)
    assert p["dari"] is None and p["akun"] is None  # dua kas kecil aktif; EXPENSE_OTHER belum terpetakan
    assert p["kurang"] == ["dari", "akun"] and not p["siap"] and len(p["pilihan_akun"]) == 2


async def test_peran_mengisi_saat_tanpa_riwayat_dan_peran_tenant_lain_diabaikan(conn):
    k, a = await _isi(conn)
    await conn.executemany("INSERT INTO account_roles VALUES ($1, $2, $3)", [
        ("kaos", "CASH_GENERAL", k["coa_bca"]), ("kaos", "EXPENSE_OTHER", a["lain"]),
        ("grap", "EXPENSE_OTHER", a["transport"])])
    p = await B.pratinjau(conn, "kaos", "bensin", 50000, HARI)
    assert p["dari"]["nama"] == "BCA" and p["akun"]["nama"] == "Beban Lain-lain" and p["siap"]


async def test_serupa_menang_atas_peran_dan_paling_sering_bukan_tebakan_akun(conn):
    k, a = await _isi(conn)
    await conn.execute("INSERT INTO account_roles VALUES ('kaos', 'EXPENSE_OTHER', $1)", a["lain"])
    for _ in range(5):
        await _biaya(conn, "kaos", k["kas_op"], a["transport"], "Bensin motor")
    await _biaya(conn, "kaos", k["toko"], a["lain"], "fotokopi")
    p = await B.pratinjau(conn, "kaos", "fotokopi", 3000, HARI)
    assert p["akun"]["nama"] == "Beban Lain-lain" and p["dari"]["nama"] == "Toko Manado"  # serupa > paling sering
    await conn.execute("DELETE FROM account_roles")
    p = await B.pratinjau(conn, "kaos", "materai", 10000, HARI)
    assert p["akun"] is None and p["dari"]["nama"] == "Kas Operasional"  # tanpa serupa/peran: akun kosong


async def test_akun_sah_hanya_beban_tenant_sendiri(conn):
    _, a = await _isi(conn)
    sah = lambda i: conn.fetchrow(B.SQL_AKUN_SAH, i, "kaos")  # noqa: E731
    assert (await sah(a["lain"]))["name"] == "Beban Lain-lain"
    for salah in (a["header"], a["pendapatan"], a["lain_tenant"]):
        assert await sah(salah) is None
