"""SO confirm/preview + kode order manual_only/'berikutnya' (3 Okt 2026, MASTER GO; pemilik "sempurnakan").
Pratinjau = jalur _konfirmasi_so yang SAMA lalu ROLLBACK (penghitung kode tak maju: dibuktikan nyata di salinan DB +
kaos). Di sini: perilaku dengan koneksi tiruan, tanpa DB."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import kode_order as KR
from app.routers import sales_orders as SO
from app.services import kode_order as KO

T = "t-uji"
SOID = str(uuid.uuid4())


def _req(body=None):
    async def js():
        return body
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": str(uuid.uuid4()), "tenant_id": T, "role": "OWNER"}),
                           json=js)


class _Tx:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, et, e, tb):
        self.log.append("rollback" if et else "commit")
        return False


class _Conn:
    def __init__(self, log):
        self.log = log

    def transaction(self):
        return _Tx(self.log)


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        conn = _Conn(self.log)

        class _A:
            async def __aenter__(s):
                return conn

            async def __aexit__(s, *a):
                return False
        return _A()


@pytest.fixture
def pool(monkeypatch):
    p = _Pool()

    async def gp():
        return p

    async def konfirmasi(conn, ctx, oid):
        return {"order_number": "SO-1", "status": "confirmed", "order_code": "005-10-26", "order_code_issued": True,
                "order_code_label": "Kode order"}
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "_konfirmasi_so", konfirmasi)
    return p


@pytest.mark.asyncio
async def test_pratinjau_konfirmasi_rollback_dan_membawa_kode(pool):
    r = await SO.preview_confirm_sales_order(_req(), SOID)
    assert pool.log == ["rollback"], pool.log  # nol tulis: transaksi TIDAK pernah commit
    assert r.data == {"order_number": "SO-1", "status": "confirmed", "order_code": "005-10-26",
                      "order_code_issued": True, "order_code_label": "Kode order", "preview": True}


@pytest.mark.asyncio
async def test_konfirmasi_asli_commit_jalur_sama(pool):
    r = await SO.confirm_sales_order(_req(), SOID)
    assert pool.log == ["commit"] and r.data["order_code"] == "005-10-26" and "preview" not in r.data


@pytest.mark.asyncio
async def test_pratinjau_galat_sama_dengan_konfirmasi(monkeypatch, pool):
    async def bukan_draf(conn, ctx, oid):
        raise HTTPException(status_code=400, detail="Cannot confirm order with status 'confirmed'")
    monkeypatch.setattr(SO, "_konfirmasi_so", bukan_draf)
    with pytest.raises(HTTPException) as e:
        await SO.preview_confirm_sales_order(_req(), SOID)
    assert e.value.status_code == 400 and pool.log == ["rollback"]


def test_rute_terpasang_ke_handler_yang_benar():
    """Pelajaran 27 Sep: helper di bawah dekorator = rute pindah ke helper."""
    rute = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in SO.router.routes}
    assert rute[("/{order_id}/confirm", ("POST",))] == "confirm_sales_order"
    assert rute[("/{order_id}/confirm/preview", ("POST",))] == "preview_confirm_sales_order"
    patch = {r.path: r.endpoint.__name__ for r in KR.router.routes if "PATCH" in r.methods}
    assert patch["/sales-orders/{order_id}/order-code"] == "ganti_kode_order"


def test_manual_only_wajib_allow_override():
    with pytest.raises(KO.KodeOrderGalat):
        KO.validasi_setelan("{SEQ}", 4, "never", "manual_only", False)
    KO.validasi_setelan("{SEQ}", 4, "never", "manual_only", True)
    KO.validasi_setelan("{SEQ}", 4, "never", "so_confirmed", False)
    with pytest.raises(KO.KodeOrderGalat):  # router setelan meneruskan allow_override
        KR._badan({"trigger": "manual_only", "allow_override": False}, dict(KO.BAWAAN))
    assert KR._badan({"trigger": "manual_only", "allow_override": True}, dict(KO.BAWAAN))["trigger"] == "manual_only"


class _KConn:
    """Koneksi tiruan untuk terbitkan_berikutnya."""

    def __init__(self, setelan, kode_so):
        self.setelan, self.kode_so, self.sql = setelan, kode_so, []
        self.argumen = []

    async def fetchrow(self, q, *a):
        self.sql.append(q)
        if "FROM order_code_settings" in q:
            return self.setelan
        if "FROM sales_orders" in q:
            return {"id": a[0], "order_number": "SO-1", "order_code": self.kode_so}
        raise AssertionError(q)

    async def execute(self, q, *a):
        self.sql.append(q)
        self.argumen.append((q, a))
        return "UPDATE 1"

    async def fetchval(self, q, *a):
        self.sql.append(q)
        if "RETURNING last_seq" in q:
            return 5
        return None


NYALA = {**KO.BAWAAN, "enabled": True, "template": "{SEQ}-{MM}-{YY}", "min_digits": 3, "reset": "monthly",
         "trigger": "manual_only", "allow_override": True}


@pytest.mark.asyncio
async def test_berikutnya_terbit_dari_penghitung_sumber_manual(monkeypatch):
    dicatat = []

    async def catat(*a, **k):
        dicatat.append(a)
    monkeypatch.setattr(KO, "_catat", catat)
    from datetime import date
    c = _KConn(NYALA, None)
    h = await KO.terbitkan_berikutnya(c, T, uuid.UUID(SOID), date(2026, 10, 3), None)
    assert h == {"status": 200, "order_code": "005-10-26", "old_code": None, "source": "manual"}
    upd = [q for q in c.sql if "UPDATE sales_orders SET order_code" in q]
    assert len(upd) == 1 and "order_code IS NULL" in upd[0]
    [(_, arg)] = [x for x in c.argumen if "UPDATE sales_orders SET order_code" in x[0]]
    assert arg[-1] == "manual" and "005-10-26" in arg  # source TERTULIS manual, bukan auto
    assert dicatat[0][6] == "manual" and dicatat[0][8] == "ORDER_CODE_ISSUED"
    kunci = [q for q in c.sql if "pg_advisory_xact_lock" in q]
    assert kunci, "kunci per-SO/periode yang sama dengan penerbitan otomatis"


@pytest.mark.asyncio
async def test_berikutnya_sudah_berkode_409_tanpa_menghabiskan_nomor():
    from datetime import date
    c = _KConn(NYALA, "001-10-26")
    h = await KO.terbitkan_berikutnya(c, T, uuid.UUID(SOID), date(2026, 10, 3), None)
    assert h == {"status": 409, "order_code": "001-10-26"}
    assert not [q for q in c.sql if "order_code_counters" in q]


@pytest.mark.asyncio
async def test_berikutnya_setelan_mati_ditolak():
    from datetime import date
    with pytest.raises(KO.KodeOrderGalat):
        await KO.terbitkan_berikutnya(_KConn({**NYALA, "enabled": False}, None), T, uuid.UUID(SOID),
                                      date(2026, 10, 3), None)


@pytest.mark.asyncio
async def test_patch_mode_tak_dikenal_422(monkeypatch):
    p = _Pool()

    async def gp():
        return p

    async def boleh(*a, **k):
        return True
    monkeypatch.setattr(KR, "get_db_pool", gp)
    monkeypatch.setattr(KO, "boleh_ganti_kode", boleh)
    with pytest.raises(HTTPException) as e:
        await KR.ganti_kode_order(_req({"mode": "acak"}), SOID)
    assert e.value.status_code == 422 and p.log == []
