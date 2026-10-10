"""Audit F1 (10 Okt 2026, MASTER GO): saldo awal persediaan = JURNAL + kartu stok, satu transaksi; saldo awal piutang
tak boleh disimpan saat create pelanggan. Law 1/4/5/6/13/16/20/23/24/27.

Penjaga: (1) urutan & isi tulisan (Dr persediaan / Cr modal saldo awal sama besar; kartu.source_id = id jurnal;
total_cost kartu = nilai jurnal); (2) akun: produk dulu, cadangan ROLE; (3) tolak nilai 0 / periode tertutup / role
tak terpetakan / ganda / di luar transaksi TANPA menulis apa pun; (4) pembulatan HALF_UP satu angka; (5) tak ada
INSERT inventory_ledger OPENING_BALANCE di luar service; (6) rute items/inventory memakai service; (7) create
pelanggan menolak ar_opening_balance>0 sebelum menyentuh DB."""
import asyncio
import inspect
import os
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException

from app.routers import customers as C
from app.routers import inventory as INV
from app.routers import items as IT
from app.routers import vendors as V
from app.schemas.customers import CreateCustomerRequest
from app.services import saldo_awal_stok as S

T = "t-uji"
U = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
PID, JID, WH = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
AKUN_INV, AKUN_EKU, AKUN_PRODUK = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
APP = Path(S.__file__).resolve().parent.parent


def _jalan(c): return asyncio.run(c)


class Konek:
    def __init__(self, tx=True, periode=None, role_ada=True, ganda=None, berjalan=False):
        self.tx, self.periode, self.role_ada, self.ganda, self.berjalan = tx, periode, role_ada, ganda, berjalan
        self.log, self.role_diminta = [], []

    def is_in_transaction(self): return self.tx

    async def fetchrow(self, sql, *a):
        sql = " ".join(sql.split())
        self.log.append(("fetchrow", sql, a))
        if "FROM fiscal_periods" in sql:
            return {"status": self.periode} if self.periode else None
        if "FROM account_roles" in sql:
            self.role_diminta.append(a[1])
            if not self.role_ada:
                return None
            return {"account_id": AKUN_INV if a[1] == "INVENTORY_MERCHANDISE" else AKUN_EKU}
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        sql = " ".join(sql.split())
        self.log.append(("fetchval", sql, a))
        if sql.startswith("SELECT EXISTS"):
            return self.berjalan
        if "SELECT journal_number FROM journal_entries" in sql:
            return self.ganda
        if "SELECT COUNT(*) FROM journal_entries" in sql:
            return 0
        if "INSERT INTO journal_entries" in sql:
            return JID
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        self.log.append(("execute", " ".join(sql.split()), a))

    def tulis(self):
        return [(k, s, a) for k, s, a in self.log if s.startswith(("INSERT", "UPDATE"))]


def _catat(k, **kw):
    d = dict(tenant_id=T, user_id=U, product_id=PID, product_code="BRG-1", product_name="Kain", tanggal=date(2026, 9, 4),
             qty=Decimal("1"), rate=Decimal("128000"), warehouse_id=WH)
    d.update(kw)
    return _jalan(S.catat_saldo_awal_stok(k, **d))


# ---------------- (1) urutan & isi ----------------
def test_jurnal_lalu_kartu_urutan_dan_isi():
    k = Konek()
    out = _catat(k)
    assert out["journal_id"] == JID and out["nilai"] == Decimal("128000.00") and out["journal_number"] == "OB-S-260904-001"
    w = k.tulis()
    assert [s.split("(")[0].strip() for _, s, _ in w] == [
        "INSERT INTO journal_entries", "INSERT INTO journal_lines", "INSERT INTO journal_lines",
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1 AND tenant_id = $2", "INSERT INTO inventory_ledger"]
    assert "'DRAFT'" in w[0][1] and "is_opening_balance" in w[0][1]
    dr, cr = w[1], w[2]
    assert dr[2][1] == AKUN_INV and dr[2][2] == Decimal("128000.00")
    assert cr[2][1] == AKUN_EKU and cr[2][2] == Decimal("128000.00") and "0, $3" in cr[1]  # kredit = nilai yang sama
    kartu = w[4]
    assert kartu[2][0] == T and kartu[2][1] == PID
    assert kartu[2][7] == JID  # source_id kartu = id jurnal (Law 6), BUKAN gen_random_uuid()
    assert kartu[2][6] == "OPENING_BALANCE" and kartu[2][11] == Decimal("128000.00")  # total_cost == nilai jurnal
    assert w[0][2][0] == T and w[0][2][5] == PID and w[0][2][6] == Decimal("128000.00")  # tenant eksplisit; jurnal.source_id = produk
    assert any("pg_advisory_xact_lock" in s and a == (f"OB_STOK:{T}",) for _, s, a in k.log)  # Law 13


def test_akun_persediaan_produk_dulu_cadangan_role():
    k = Konek()
    _catat(k, inventory_account_id=AKUN_PRODUK)
    assert k.tulis()[1][2][1] == AKUN_PRODUK and k.role_diminta == ["EQUITY_OPENING_BALANCE"]
    k2 = Konek()
    _catat(k2)
    assert k2.role_diminta == ["INVENTORY_MERCHANDISE", "EQUITY_OPENING_BALANCE"]


def test_pembulatan_half_up_satu_angka_untuk_jurnal_dan_kartu():
    k = Konek()
    out = _catat(k, qty=Decimal("3"), rate=Decimal("33.335"))  # 100,005 -> 100,01
    w = k.tulis()
    assert out["nilai"] == Decimal("100.01") and w[1][2][2] == w[2][2][2] == w[4][2][11] == Decimal("100.01")
    assert S.nilai_saldo_awal(3, 33.335) == Decimal("100.01")


def test_movement_type_dan_catatan_bisa_dipilih_pemanggil_inventory():
    k = Konek()
    _catat(k, product_code=None, movement_type="IN", catatan="Initial stock from product creation")
    kartu = k.tulis()[4]
    assert kartu[2][4] == "IN" and kartu[2][13] == "Initial stock from product creation"
    assert kartu[2][8] == "OB-S-260904-001"  # tanpa kode barang -> nomor jurnal


# ---------------- (3) penolakan tanpa tulisan ----------------
@pytest.mark.parametrize("rate", [0, None, Decimal("0")])
def test_nilai_nol_ditolak_tanpa_menulis(rate):
    k = Konek()
    with pytest.raises(HTTPException) as e:
        _catat(k, rate=rate)
    assert e.value.status_code == 422 and "harga pokok" in e.value.detail and not k.log


def test_usaha_sudah_berjalan_ditolak_arahkan_ke_penyesuaian_stok_tanpa_menulis():
    k = Konek(berjalan=True)
    with pytest.raises(HTTPException) as e:
        _catat(k)
    assert e.value.status_code == 422 and "Penyesuaian stok" in e.value.detail and not k.tulis()
    # pra-pemeriksaan rute: sama, dan hanya bila ada saldo awal (qty>0)
    with pytest.raises(HTTPException) as e2:
        _jalan(S.periksa_saldo_awal(Konek(berjalan=True), T, 2, 5))
    assert e2.value.status_code == 422 and "Penyesuaian stok" in e2.value.detail
    k3 = Konek(berjalan=True)
    assert _jalan(S.periksa_saldo_awal(k3, T, 0, None)) == 0 and not k3.log  # tanpa saldo awal: DB tak disentuh


def test_predikat_sql_sama_dengan_trigger_v114():
    import pathlib
    mig = next(pathlib.Path(__file__).resolve().parents[2].glob("../migrations/V114__opening_balance_guard.sql"), None)
    sq = " ".join(S.SQL_SUDAH_BERJALAN.split())
    for kunci in ("'OPENING', 'OPENING_BALANCE', 'OPENING_BALANCE_REVERSAL'", "je.status = 'POSTED'", "je.source_type = 'REVERSAL'", "je.tenant_id = $1"):
        assert kunci in sq, kunci
    if mig and mig.exists():  # bila berkas migrasi ada di pohon uji, daftar tipenya HARUS sama
        t = " ".join(mig.read_text().split())
        assert "'OPENING', 'OPENING_BALANCE', 'OPENING_BALANCE_REVERSAL'" in t


def test_periksa_nilai_tanpa_qty_bukan_saldo_awal():
    assert S.periksa_nilai(None, None) == 0 and S.periksa_nilai(0, 5) == 0
    assert S.periksa_nilai(2, 5) == Decimal("10.00")
    with pytest.raises(HTTPException):
        S.periksa_nilai(2, None)


def test_periode_tertutup_ditolak_ramah_tanpa_menulis():
    k = Konek(periode="CLOSED")
    with pytest.raises(HTTPException) as e:
        _catat(k)
    assert e.value.status_code == 400 and "ditutup" in e.value.detail and not k.tulis()


def test_role_tak_terpetakan_409_ramah_tanpa_menulis():
    k = Konek(role_ada=False)
    with pytest.raises(HTTPException) as e:
        _catat(k)
    assert e.value.status_code == 409 and "Persediaan" in e.value.detail and not k.tulis()


def test_saldo_awal_ganda_untuk_barang_sama_ditolak():
    k = Konek(ganda="OB-S-260904-001")
    with pytest.raises(HTTPException) as e:
        _catat(k)
    assert e.value.status_code == 409 and "OB-S-260904-001" in e.value.detail and not k.tulis()


def test_wajib_di_dalam_transaksi_pemanggil():
    k = Konek(tx=False)
    with pytest.raises(RuntimeError):
        _catat(k)
    assert not k.log


# ---------------- (5) satu-satunya penulis ----------------
def test_tak_ada_insert_kartu_saldo_awal_di_luar_service():
    pelanggar = []
    for f in APP.rglob("*.py"):
        if "tests" in f.parts or f.name == "saldo_awal_stok.py":
            continue
        t = f.read_text()
        for m in re.finditer(r"INSERT\s+INTO\s+(?:public\.)?inventory_ledger", t, re.I):
            if "OPENING_BALANCE" in t[m.start(): m.start() + 900]:
                pelanggar.append(f.name)
    assert not pelanggar, f"INSERT inventory_ledger OPENING_BALANCE di luar service saldo_awal_stok: {pelanggar}"


# ---------------- (6) rute memakai service ----------------
def test_rute_items_memakai_service_dalam_transaksi():
    src = inspect.getsource(IT.create_item)
    assert "INSERT INTO inventory_ledger" not in src and "gen_random_uuid()" not in src
    assert src.index("periksa_saldo_awal(") < src.index("async with conn.transaction()") < src.index("catat_saldo_awal_stok(")
    assert "inventory_account_id=body.inventory_account_id" in src


def test_rute_inventory_produk_dan_saldo_awal_atomik():
    src = inspect.getsource(INV.add_product)
    assert "INSERT INTO inventory_ledger" not in src
    assert src.index("periksa_saldo_awal(") < src.index("async with conn.transaction()") < src.index("INSERT INTO public.products") < src.index("catat_saldo_awal_stok(")
    assert 'movement_type="IN"' in src


def test_create_item_nilai_nol_atau_usaha_berjalan_ditolak_sebelum_transaksi(monkeypatch):
    ber = {"v": False}

    class K:
        async def fetchrow(self, *a): return None
        async def fetchval(self, sql, *a): return ber["v"] if " ".join(sql.split()).startswith("SELECT EXISTS") else None
        def transaction(self): raise AssertionError("transaksi tak boleh dibuka")
        async def close(self): pass

    async def konek(): return K()

    async def auto(conn, tid, tipe, trk, inv, cogs): return inv, cogs
    monkeypatch.setattr(IT, "get_db_connection", konek)
    monkeypatch.setattr(IT, "_auto_default_product_accounts", auto)
    req = type("R", (), {"state": type("S", (), {"user": {"tenant_id": T, "user_id": U}})()})()
    body = IT.CreateItemRequest(name="Kain Uji", item_type="goods", track_inventory=True, base_unit="pcs", opening_stock=5)
    with pytest.raises(HTTPException) as e:
        _jalan(IT.create_item(req, body))
    assert e.value.status_code == 422 and "harga pokok" in e.value.detail
    ber["v"] = True  # harga pokok ada, tapi usaha sudah berjalan
    body2 = IT.CreateItemRequest(name="Kain Uji", item_type="goods", track_inventory=True, base_unit="pcs", opening_stock=5, opening_stock_rate=100)
    with pytest.raises(HTTPException) as e2:
        _jalan(IT.create_item(req, body2))
    assert e2.value.status_code == 422 and "Penyesuaian stok" in e2.value.detail


# ---------------- (7) pelanggan ----------------
def test_create_pelanggan_menolak_saldo_awal_piutang_sebelum_db(monkeypatch):
    dipanggil = []

    async def pool(): dipanggil.append(1); raise RuntimeError("tak boleh")
    monkeypatch.setattr(C, "get_pool", pool)
    req = type("R", (), {"state": type("S", (), {"user": {"tenant_id": T, "user_id": U}})()})()
    with pytest.raises(HTTPException) as e:
        _jalan(C.create_customer(req, CreateCustomerRequest(name="Budi", ar_opening_balance=5000)))
    assert e.value.status_code == 422 and "Saldo awal" in e.value.detail and not dipanggil
    # kontrol: tanpa saldo awal, handler MAJU ke DB (alat ukur bisa gagal -> pagarnya yang memblok, bukan kebetulan)
    with pytest.raises(HTTPException):
        _jalan(C.create_customer(req, CreateCustomerRequest(name="Budi", ar_opening_balance=0)))
    assert dipanggil == [1]


def test_create_vendor_menolak_saldo_awal_hutang_sebelum_db(monkeypatch):
    dipanggil = []

    async def pool(): dipanggil.append(1); raise RuntimeError("tak boleh")
    monkeypatch.setattr(V, "get_pool", pool)
    req = type("R", (), {"state": type("S", (), {"user": {"tenant_id": T, "user_id": U}})()})()
    fungsi = [v for n, v in vars(V).items() if n == "create_vendor"][0]
    skema = inspect.signature(fungsi).parameters["body"].annotation
    with pytest.raises(HTTPException) as e:
        _jalan(fungsi(req, skema(name="PT Uji", opening_balance=7000)))
    assert e.value.status_code == 422 and "Saldo awal hutang" in e.value.detail and not dipanggil
    with pytest.raises(HTTPException):  # kontrol: tanpa saldo awal handler MAJU ke DB
        _jalan(fungsi(req, skema(name="PT Uji", opening_balance=0)))
    assert dipanggil == [1]
