"""Serial S2 (27 Nov 2026, flag gelap serial_v1): KELUAR — Surat Jalan dari pesanan (sj_so) + kirim faktur (_execute_fulfillment)
meneruskan nomor seri per baris ke pintu kartu stok; pratinjau SJ menilai aturan yang sama (serial_gerak.blok_serial)."""
import asyncio
import inspect
import uuid

from app.services import serial_gerak as SG


class K:
    def __init__(self, track, flag=True):
        self.track, self.flag = track, flag

    async def fetchval(self, sql, *a):
        assert "track_serial" in sql
        return self.track

    async def fetch(self, sql, *a):
        assert "tenant_features" in sql
        return [{"feature": "serial_v1"}] if self.flag else []


def _b(k, serials, qty=2, pid=uuid.uuid4()):
    return asyncio.run(SG.blok_serial(k, "t", pid, "Laptop", serials, qty))


def test_blok_serial_aturan_sama_dengan_pintu():
    assert _b(K(True), ["A", "B"]) is None
    assert _b(K(True), None)[0] == "SERIAL_WAJIB"
    assert _b(K(True), ["A"])[0] == "SERIAL_JUMLAH_TAK_SAMA"
    assert _b(K(True), ["A", "a"])[0] == "SERIAL_KEMBAR"
    assert _b(K(False), ["A", "B"])[0] == "SERIAL_TAK_DILACAK"
    assert _b(K(False), None) is None
    assert _b(K(True, flag=False), None) is None
    assert asyncio.run(SG.blok_serial(K(True), "t", None, "Jasa", None)) is None


def test_sj_so_meneruskan_serial_dan_pratinjau_menilai():
    from app.services import sj_so as S
    assert "serials" in S.BarisIn.model_fields
    src = inspect.getsource(S)
    assert 'serials=l.get("serials")' in src and "SG.blok_serial(" in src
    assert 'b["batch"] or (b["serial"] and not serial_v1)' in src          # batch tetap belum didukung; serial bila flag nyala
    assert '(b["stok"] and b["serial"]) or ln["serials"] is not None' in src   # barang biasa: nol kueri tambahan


def test_kirim_faktur_meneruskan_serial():
    from app.routers import sales_invoices as SI
    src = inspect.getsource(SI)
    assert '"serials": i.get("serials")' in src and 'serials=req.get("serials")' in inspect.getsource(SI._execute_fulfillment)
