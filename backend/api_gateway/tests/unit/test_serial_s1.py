"""Serial S1 (27 Nov 2026; DESAIN-serial-20261127; V513): satu penulis nomor seri di pintu kartu stok, flag gelap serial_v1.
Barang tanpa track_serial: satu SELECT, perilaku lama. track_serial + flag: wajib serial (|serial| = qty), masuk/keluar/balik + taut + jejak."""
import asyncio
import pathlib
import re
import uuid

import pytest

from app.services import serial_gerak as SG
from app.services.galat_berkode import GalatBerkode

T, P, G, G2 = "t-uji", uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
MIG = pathlib.Path(__file__).resolve().parents[3] / "migrations"


class K:
    """Koneksi tiruan: item_serials di memori; mencatat taut + jejak."""
    def __init__(self, track=True, flag=True, serial=()):
        self.track, self.flag, self.taut, self.jejak = track, flag, [], []
        self.ser = {s["serial_number"].upper(): dict(s) for s in serial}

    async def fetchval(self, sql, *a):
        if "track_serial" in sql:
            return self.track
        if "INSERT INTO item_serials" in sql:
            i = uuid.uuid4()
            self.ser[a[2].upper()] = {"id": i, "serial_number": a[2], "status": "available", "warehouse_id": a[3]}
            return i
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        if "tenant_features" in sql:
            return [{"feature": "serial_v1"}] if self.flag else []
        if "FROM item_serials" in sql:
            return [s for k, s in self.ser.items() if k in a[2]]
        if "FROM inventory_ledger_serials t" in sql:
            return [{**t, "serial_number": self._s(t["serial_id"])["serial_number"], "status": self._s(t["serial_id"])["status"],
                     "warehouse_id": self._s(t["serial_id"])["warehouse_id"],
                     "bergerak": any(x["serial_id"] == t["serial_id"] and x["urut"] > t["urut"] for x in self.taut)}
                    for t in self.taut if t["ledger_id"] == a[0]]
        raise AssertionError(sql)

    def _s(self, sid):
        return next(s for s in self.ser.values() if s["id"] == sid)

    async def execute(self, sql, *a):
        if "UPDATE item_serials" in sql:
            s = self._s(a[0])
            s["status"] = a[2]
            if len(a) > 3:
                s["warehouse_id"] = a[3]
        elif "inventory_ledger_serials" in sql:
            self.taut.append({"ledger_id": a[0], "serial_id": a[1], "arah": a[3], "status_sebelum": a[4], "gudang_sebelum": a[5], "urut": len(self.taut)})
        elif "serial_movements" in sql:
            self.jejak.append(a)
        else:
            raise AssertionError(sql)


def _kartu(k, masuk=0, keluar=0, serials=None, balik=None, gudang=G, src="GOODS_RECEIPT", lid=None):
    lid = lid or uuid.uuid4()
    asyncio.run(SG.pada_kartu(k, tenant_id=T, product_id=P, product_name="Laptop", warehouse_id=gudang, ledger_id=lid, quantity_in=masuk,
                              quantity_out=keluar, serials=serials, balik_ledger=balik, ref={"source_type": src}))
    return lid


def _galat(fn):
    with pytest.raises(GalatBerkode) as e:
        fn()
    return e.value


def test_tanpa_track_serial_tak_berubah_dan_serial_ditolak():
    k = K(track=False)
    _kartu(k, masuk=2)
    assert not k.taut and not k.jejak
    assert _galat(lambda: _kartu(k, masuk=1, serials=["A"])).code == "SERIAL_TAK_DILACAK"


def test_flag_mati_tak_berubah():
    k = K(flag=False)
    _kartu(k, masuk=2)
    assert not k.taut


def test_wajib_serial_dan_jumlah():
    k = K()
    assert _galat(lambda: _kartu(k, masuk=2)).code == "SERIAL_WAJIB"
    assert _galat(lambda: _kartu(k, masuk=2, serials=["A"])).code == "SERIAL_JUMLAH_TAK_SAMA"
    assert _galat(lambda: _kartu(k, masuk=1.5, serials=["A"])).code == "SERIAL_QTY_PECAHAN"
    assert _galat(lambda: _kartu(k, masuk=2, serials=["A", " a "])).code == "SERIAL_KEMBAR"


def test_masuk_lalu_keluar_lalu_balik():
    k = K()
    lm = _kartu(k, masuk=2, serials=["SN1", "SN2"])
    assert {s["status"] for s in k.ser.values()} == {"available"} and len(k.taut) == 2
    assert _galat(lambda: _kartu(k, masuk=1, serials=["sn1"])).code == "SERIAL_SUDAH_ADA"
    assert _galat(lambda: _kartu(k, keluar=1, serials=["SN1"], gudang=G2)).code == "SERIAL_TAK_TERSEDIA"
    assert _galat(lambda: _kartu(k, keluar=1, serials=["SN9"])).code == "SERIAL_TAK_TERSEDIA"
    lk = _kartu(k, keluar=1, serials=["SN1"], src="STOCK_ADJUSTMENT")
    assert k.ser["SN1"]["status"] == "adjusted_out"
    assert _galat(lambda: _kartu(k, masuk=0, keluar=2, balik=lm)).code == "SERIAL_SUDAH_BERGERAK"   # SN1 sudah keluar sesudah masuk
    _kartu(k, masuk=1, balik=lk)                                                                     # void keluar -> SN1 kembali
    assert (k.ser["SN1"]["status"], k.ser["SN1"]["warehouse_id"]) == ("available", G)


def test_balik_masuk_serial_lahir_jadi_scrapped():
    k = K()
    lm = _kartu(k, masuk=1, serials=["SN5"])
    _kartu(k, keluar=1, balik=lm)
    assert k.ser["SN5"]["status"] == "scrapped" and len(k.jejak) == 2


def test_masuk_ulang_serial_yang_sudah_keluar():
    k = K(serial=[{"id": uuid.uuid4(), "serial_number": "SN7", "status": "sold", "warehouse_id": G}])
    _kartu(k, masuk=1, serials=["SN7"], gudang=G2)
    assert (k.ser["SN7"]["status"], k.ser["SN7"]["warehouse_id"]) == ("available", G2)
    assert k.taut[0]["status_sebelum"] == "sold"


def test_pintu_kartu_memanggil_serial_dan_pembalik_membawa_baris_asal():
    import inspect
    from app.services import inventory_helpers as IH, ledger_kartu as LK
    assert "serial_gerak.pada_kartu" in inspect.getsource(LK.tulis_kartu)
    assert "balik_ledger=entry[\"id\"]" in inspect.getsource(IH.record_inventory_reversal)
    for f in (IH.record_inventory_inbound, IH.record_inventory_outbound):
        assert "serials=serials" in inspect.getsource(f)


def test_grn_meneruskan_serial_per_baris():
    import inspect
    from app.routers import purchase_orders as PO
    from app.schemas.purchase_orders import ReceiveItemRequest
    from app.services import grn_rencana as GR
    assert "serials" in ReceiveItemRequest.model_fields
    assert '(serials or {}).get(str(b["po_item_id"]))' in inspect.getsource(GR.tulis_grn)
    assert "i.serials for i in body.items" in inspect.getsource(PO)


def test_migrasi_v513_aditif_dan_pembalik():
    up = (MIG / "V513__serial_taut_ledger.sql").read_text()
    rb = (MIG / "V513__serial_taut_ledger_ROLLBACK.sql").read_text()
    assert "CREATE TABLE inventory_ledger_serials" in up and "chk_item_serials_status" in up
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", up, re.M | re.I)
    assert "DROP TABLE IF EXISTS inventory_ledger_serials" in rb and "DROP CONSTRAINT IF EXISTS chk_item_serials_status" in rb


def test_penyesuaian_menyimpan_serial_di_draf_dan_meneruskan_saat_posting():
    import inspect
    from app.routers import stock_adjustments as SA
    from app.schemas.stock_adjustments import StockAdjustmentItemCreate, StockAdjustmentItemUpdate
    from app.services import stok_sesuai as SS
    assert "serials" in StockAdjustmentItemCreate.model_fields and "serials" in StockAdjustmentItemUpdate.model_fields
    src = inspect.getsource(SA._tulis_baris_draf)
    assert '", serials" if b.get("serials") is not None else ""' in src   # kolom hanya disebut bila ada serial (aman sebelum migrasi)
    assert inspect.getsource(SA).count('serials=g.get("serials")') == 2   # masuk + keluar
    s = inspect.getsource(SS)
    assert '"serials": i.get("serials")' in s and "SERIAL_TAK_DILACAK" in s


def test_migrasi_v513_kolom_draf_penyesuaian():
    up = (MIG / "V513__serial_taut_ledger.sql").read_text()
    rb = (MIG / "V513__serial_taut_ledger_ROLLBACK.sql").read_text()
    assert "ALTER TABLE stock_adjustment_items ADD COLUMN serials TEXT[]" in up
    assert "ALTER TABLE stock_adjustment_items DROP COLUMN IF EXISTS serials" in rb
