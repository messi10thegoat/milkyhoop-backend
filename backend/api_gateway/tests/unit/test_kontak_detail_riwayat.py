"""B2 detail + B4 riwayat Pelanggan / Vendor (4 Nov 2026): aksi boleh/alasan dari pemeriksa PENULIS (satu aturan), total
penjualan/pembelian dari jurnal, riwayat bentuk SO dari sumber yang SAMA dengan /activity."""
import asyncio
import inspect
from datetime import datetime, timezone
from decimal import Decimal

from app.routers import customers as CU
from app.routers import vendors as VE
from app.services import kontak_detail as KDT
from app.services import kontak_riwayat as KR
from app.services import smart_delete as SD
from app.services import vendor_pembelian as VP


def test_aksi_detail_urutan_dan_alasan():
    a = KDT.aksi_detail(True, None, None)
    assert [x["kunci"] for x in a] == ["ubah", "saldo_awal", "gabung", "nonaktifkan", "hapus"]
    assert a[0]["utama"] is True and a[-1]["bahaya"] is True and all(x["boleh"] for x in a)
    b = KDT.aksi_detail(False, "Tidak bisa menghapus pelanggan yang sudah memiliki transaksi. Nonaktifkan saja.", "JV-OB-0001")
    k = {x["kunci"]: x for x in b}
    assert "aktifkan" in k and "nonaktifkan" not in k
    assert k["hapus"]["boleh"] is False and k["hapus"]["alasan"].startswith("Tidak bisa menghapus")
    assert k["saldo_awal"]["boleh"] is False and "JV-OB-0001" in k["saldo_awal"]["alasan"]


def test_teks_server():
    assert (KDT.termin_text(30), KDT.termin_text(0), KDT.termin_text(None)) == ("Net 30 hari", "Tunai", "Tunai")
    assert (KDT.tipe_text("BADAN"), KDT.tipe_text("orang_pribadi"), KDT.tipe_text(None)) == ("Badan usaha", "Orang pribadi", None)


class _Hitung:
    def __init__(self, ada):
        self.ada = ada

    async def fetchval(self, sql, eid):
        return self.ada.get(sql.split("FROM ")[1].split(" ")[0], 0)


def test_cek_jejak_satu_aturan_dengan_smart_delete():
    cek = [("sales_invoices", "customer_id", "Ada faktur {name}."), ("receive_payments", "customer_id", None)]
    assert asyncio.run(SD.cek_jejak(_Hitung({}), "x", "Budi", cek)) is None
    assert asyncio.run(SD.cek_jejak(_Hitung({"sales_invoices": 2}), "x", "Budi", cek)) == "Ada faktur Budi."
    assert "await cek_jejak(" in inspect.getsource(SD.smart_delete)


def test_penulis_dan_detail_memakai_pemeriksa_sama():
    assert "ledger_checks=JEJAK_PELANGGAN" in inspect.getsource(CU.delete_customer)
    assert "ledger_checks=JEJAK_VENDOR" in inspect.getsource(VE.delete_vendor)
    cd, vd = inspect.getsource(CU.get_customer), inspect.getsource(VE.get_vendor)
    assert "JEJAK_PELANGGAN" in cd and "saldo_awal_pelanggan(" in cd and "KDT.aksi_detail(" in cd
    assert "JEJAK_VENDOR" in vd and "saldo_awal_vendor(" in vd and "pembelian_vendor(" in vd
    assert "SUM(total_amount)" not in cd and "compute_vendor_ap" not in vd  # total dari jurnal; saldo dari kontak_daftar
    src_cu, src_ve = inspect.getsource(CU), inspect.getsource(VE)
    assert src_cu.count("await saldo_awal_pelanggan(") == 2 and src_ve.count("await saldo_awal_vendor(") == 2  # penulis + detail
    assert "kredit_vendor" not in vd  # belum ada penentu jurnal -> tak dikirim


def test_pembelian_vendor_turunan_jurnal_tanpa_saldo_awal():
    q = VP.SQL_PEMBELIAN
    assert "source_type = 'BILL'" in q and "source_type = 'VENDOR_CREDIT'" in q and "account_type = 'PAYABLE'" in q
    assert q.count("NOT COALESCE(b.is_opening_balance, false)") == 2 and "reversed_by_id IS NULL" in q


class _Riwayat:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        return self.rows

    async def fetchval(self, sql, *a):
        if "timezone" in sql or "zona" in sql:
            return "Asia/Jakarta"
        return len(self.rows)

    async def fetchrow(self, sql, *a):
        return {"timezone": "Asia/Jakarta"}


def _r(t, tipe, desc, total=None, nomor=None, aktor=None):
    return {"id": "1", "type": tipe, "description": desc, "details": None, "actor_name": aktor,
            "timestamp": datetime(2026, 11, 4, t, 0, tzinfo=timezone.utc), "document_number": nomor, "total": total}


def test_riwayat_bentuk_so_rupiah_dan_saring_izin(monkeypatch):
    from app.utils import tanggal_tenant as TT

    async def zona(conn, tid):
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Makassar")
    monkeypatch.setattr(TT, "zona_tenant", zona)
    rows = [_r(5, "sale", "Faktur penjualan INV-1", Decimal("1500000.00"), "INV-1"), _r(4, "payment", "Pembayaran RCV-1", Decimal("500000"), "RCV-1"),
            _r(3, "updated", "Data pelanggan diubah", aktor="Anton")]

    async def boleh(modul):
        return modul != "receive_payment"
    d = asyncio.run(KR.riwayat(_Riwayat(rows), "t", "customer", "c", boleh))
    assert [e["jenis"] for e in d["events"]] == ["sale", "updated"] and d["omitted"] == ["receive_payment"]
    e = d["events"][0]
    assert e["ringkas"] == "Faktur penjualan INV-1 · Rp 1.500.000" and e["at"] == "2026-11-04T13:00:00+08:00"
    assert e["dokumen"] == {"tipe": "sales_invoice", "id": "1", "nomor": "INV-1"} and e["aktor"] is None
    assert d["events"][1]["aktor"] == {"nama": "Anton"} and d["events"][1]["dokumen"] is None


def test_activity_lama_dan_history_satu_sumber():
    for mod, fn in ((CU, "get_customer_activity"), (VE, "get_vendor_activity")):
        s = inspect.getsource(getattr(mod, fn))
        assert "await KR.baris(" in s and "UNION ALL" not in s, fn
        paths = [getattr(r, "path", "") for r in mod.router.routes]
        assert any(p.endswith("/history") for p in paths), mod.__name__
    assert KR.SQL["vendor"]["dokumen"]["payment"] == ("payment_out", "send_payment")
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur("/api/customers/abc/history", "GET") == ("customer", "R")
    assert izin_untuk_jalur("/api/vendors/abc/history", "GET") == ("supplier", "R")
