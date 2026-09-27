"""SATU aturan ubah tipe/pelacakan barang untuk form (PUT) DAN impor massal (27 Sep 2026, putusan pemilik via MASTER).

Dulu: PUT = E2b ketat (SEMUA ubah tipe ditolak bila pernah bertransaksi), impor = Q-018 sempit -> dua permukaan,
dua aturan. Kini per ARAH (pola Xero/QuickBooks):
 (1) dilacak -> tak dilacak : tolak HANYA bila pendapatan tertunda / riwayat ledger
 (2) tak dilacak -> dilacak : tolak bila sudah ada faktur/tagihan/ledger (E2b)
 (3) jasa <-> non_inventory : aturan sempit seperti (1)
Handler PUT DAN impor DIJALANKAN dengan koneksi tiruan yang sama (keadaan DB per kasus); hasil keduanya dibandingkan.
Pelacakan ikut tipe (non_inventory/jasa tak pernah dilacak) di kedua jalur.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.routers.items as IT
from app.schemas.items import UpdateItemRequest

T = "kaos-biru-konveksi"
PID = uuid.uuid4()


class Rec(dict):
    def __getitem__(self, k):
        return self.get(k)


class Lewat(BaseException):
    """penjaga dilewati -> handler masuk transaksi tulis"""


class Conn:
    def __init__(self, tipe, lacak, tunda=None, ledger=False, transaksi=False, tx_lewat=True):
        self.tipe, self.lacak, self.tunda, self.ledger, self.transaksi = tipe, lacak, tunda, ledger, transaksi
        self.tx_lewat, self.tulis = tx_lewat, []

    def transaction(self):
        c = self

        class _Tx:
            async def __aenter__(self):
                if c.tx_lewat:
                    raise Lewat()

            async def __aexit__(self, *a):
                return False
        return _Tx()

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if "FROM products WHERE id = $1 AND tenant_id = $2" in s and s.startswith("SELECT id FROM"):
            return Rec(id=PID)
        if "item_code = $2" in s:
            return Rec(id=PID)
        if "nama_produk = $2 AND id != $3" in s:
            return None
        if "SELECT item_type, track_inventory FROM products" in s:
            assert a[1] == T
            return Rec(item_type=self.tipe, track_inventory=self.lacak)
        if "FROM products WHERE id = $1 AND tenant_id = $2" in s:   # old_item
            return Rec(item_type=self.tipe, track_inventory=self.lacak, bisa_dikirim=False, nama_produk="Kaos")
        raise AssertionError(s[:90])

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        assert T in a, s[:80]
        if "string_agg(DISTINCT si.invoice_number" in s:
            return self.tunda
        if "bill_items" in s:
            return self.transaksi or self.ledger
        if "FROM inventory_ledger WHERE product_id = $1 AND tenant_id = $2" in s:
            return self.ledger
        raise AssertionError(s[:90])

    async def execute(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))

    async def close(self):
        pass


def _req(body=None):
    async def j():
        return body
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "u1", "role": "OWNER"}), json=j)


async def _put(monkeypatch, conn, **ubah):
    async def g():
        return conn
    monkeypatch.setattr(IT, "get_db_connection", g)
    monkeypatch.setattr(IT, "get_user_context", lambda r: {"tenant_id": T, "user_id": "u1"})
    body = UpdateItemRequest(**ubah)
    try:
        await IT.update_item(_req(), PID, body)
    except Lewat:
        return "LOLOS", body
    except HTTPException as e:
        if e.status_code == 400 and "tidak bisa diubah" in str(e.detail):
            return "TOLAK", str(e.detail)
        raise
    return "LOLOS", body


async def _impor(monkeypatch, conn, tipe):
    conn.tx_lewat = False

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    async def pool():
        return SimpleNamespace(acquire=lambda: _Acq())
    monkeypatch.setattr(IT, "get_pool", pool)
    monkeypatch.setattr(IT, "get_user_context", lambda r: {"tenant_id": T, "user_id": "u1"})
    r = await IT.bulk_import_items(_req({"items": [{"code": "K-1", "name": "Kaos", "type": tipe}]}))
    upd = [a for s, a in conn.tulis if s.startswith("UPDATE products SET")]
    errs = (r.get("data") or r).get("errors") if isinstance(r, dict) else None
    return ("TOLAK" if upd and upd[0][1] is None else "LOLOS"), upd[0], errs


KASUS = [
    # (nama, tipe lama, lacak lama, keadaan DB, tipe baru, hasil)
    ("1 goods->non_inv, pernah terjual tanpa tunda/ledger", "goods", True, {"transaksi": True}, "non_inventory", "LOLOS"),
    ("1 goods->non_inv, pendapatan tertunda", "goods", True, {"tunda": "INV-2609-0009", "transaksi": True}, "non_inventory", "TOLAK"),
    ("1 goods->non_inv, riwayat ledger", "goods", True, {"ledger": True}, "non_inventory", "TOLAK"),
    ("2 non_inv->goods, pernah terjual", "non_inventory", False, {"transaksi": True}, "goods", "TOLAK"),
    ("2 non_inv->goods, belum pernah", "non_inventory", False, {}, "goods", "LOLOS"),
    ("3 jasa->non_inv, pernah terjual", "service", False, {"transaksi": True}, "non_inventory", "LOLOS"),
    ("3 non_inv->jasa, pendapatan tertunda", "non_inventory", False, {"tunda": "INV-1", "transaksi": True}, "service", "TOLAK"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("nama,tl,ll,db,tb,harap", KASUS, ids=[k[0] for k in KASUS])
async def test_put_dan_impor_satu_aturan(monkeypatch, nama, tl, ll, db, tb, harap):
    hp, info = await _put(monkeypatch, Conn(tl, ll, **db), item_type=tb)
    hi, upd, errs = await _impor(monkeypatch, Conn(tl, ll, **db), tb)
    assert hp == harap, (nama, "PUT", info)
    assert hi == harap, (nama, "impor", upd, errs)
    if harap == "TOLAK" and db.get("tunda"):
        assert db["tunda"] in info and "Akui pendapatan" in info
    if harap == "LOLOS":
        lacak_harap = tb == "goods"
        efektif = info.track_inventory if info.track_inventory is not None else ll
        assert efektif is lacak_harap, ("PUT lacak", info.track_inventory, ll)
        assert upd[1] == tb and upd[-1] is lacak_harap, ("impor lacak", upd)


@pytest.mark.asyncio
async def test_goods_matikan_lacak_saja_arah_1(monkeypatch):
    assert (await _put(monkeypatch, Conn("goods", True, transaksi=True), track_inventory=False))[0] == "LOLOS"
    assert (await _put(monkeypatch, Conn("goods", True, ledger=True), track_inventory=False))[0] == "TOLAK"


@pytest.mark.asyncio
async def test_tanpa_ubah_tipe_tak_ditanya(monkeypatch):
    c = Conn("goods", True, ledger=True, tunda="INV-X", transaksi=True)
    assert (await _put(monkeypatch, c, name="Kaos baru"))[0] == "LOLOS"
