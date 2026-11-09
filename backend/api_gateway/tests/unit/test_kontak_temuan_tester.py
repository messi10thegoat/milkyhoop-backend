"""Temuan TESTER nyawit 9 Nov (Kontak LIVE): A HP vendor di kontak_teks · B urutan mutasi dalam tanggal = waktu buat jurnal · C teks riwayat
pelanggan seragam vendor (penentu bersama KDT.teks_ubah; baris lama diterjemahkan saat tampil)."""
import asyncio
import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path

from app.services import kontak_daftar as KD
from app.services import kontak_detail as KDT
from app.services import kontak_mutasi as KM
from app.services import kontak_riwayat as KR

APP = Path(KD.__file__).resolve().parents[1]
WIB = dt.timezone(dt.timedelta(hours=7))


# ---------------------------------------------------------------- A
def test_hp_vendor_dipakai_bila_telepon_kosong():
    v = {"id": uuid.uuid4(), "is_active": True, "mobile_phone": "081243065632", "phone": None, "city": "Manado"}
    assert KD.medan_baris("vendor", v, {})["kontak_teks"] == "0812 4306 5632 · Manado"
    assert KD.telepon_baris({"mobile_phone": " ", "phone": "0431 123456"}, ("mobile_phone", "phone")) == "0431 123456"
    assert KD.telepon_baris({"mobile_phone": "0812", "phone": "0431"}, ("mobile_phone", "phone")) == "0812"  # HP dulu
    c = {"id": uuid.uuid4(), "is_active": True, "telepon": "081299990000", "city": None}
    assert KD.medan_baris("customer", c, {})["kontak_teks"] == "0812 9999 0000"  # pelanggan tak berubah


def test_daftar_vendor_memilih_mobile_phone():
    src = (APP / "routers/vendors.py").read_text()
    assert src.count("contact_person, phone, mobile_phone, email, city,") == 2 and "v.phone, v.mobile_phone, v.email" in src


# ---------------------------------------------------------------- B
def _b(urut, jno, jam, debit=0, kredit=0, dno=None):
    return {"urut": urut, "st": "INVOICE", "tgl": dt.date(2026, 11, 9), "jid": uuid.uuid4(), "jno": jno, "tipe": None, "did": None, "dno": dno,
            "ket": None, "debit": Decimal(debit), "kredit": Decimal(kredit),
            "dibuat": dt.datetime(2026, 11, 9, jam[0], jam[1], tzinfo=WIB) if jam else None}


def test_urut_dalam_tanggal_kronologis_saldo_tak_negatif():
    # vendor: tagihan 10:43 (kredit = hutang naik), pembayaran 11:37 -- nomor jurnal pembayaran lebih kecil (dulu tampil duluan)
    bayar, tagihan = _b(2, "BP-0013", (11, 37), debit=3480000), _b(1, "BILL-0016", (10, 43), kredit=3480000)
    r = KM.susun([bayar, tagihan], "vendor", None, 0, 50)
    assert [x["jno"] for x in r["baris"]] == ["BILL-0016", "BP-0013"]
    assert all(x["saldo"] >= 0 for x in r["baris"]) and r["saldo_akhir"] == 0


def test_urut_waktu_kosong_aman_dan_saldo_akhir_tetap():
    a, b, c = _b(1, "Z", None, debit=100), _b(2, "A", (9, 0), kredit=40), _b(1, "M", (8, 0), debit=5)
    r1 = KM.susun([a, b, c], "customer", None, 0, 50)
    assert [x["jno"] for x in r1["baris"]] == ["Z", "M", "A"] and r1["saldo_akhir"] == Decimal(65)


def test_mutasi_membaca_waktu_buat_bersaring_tenant():
    jid = uuid.uuid4()
    baris = [{**_b(1, "INV-1", None, debit=10), "jid": jid}]
    for r in baris:
        r.pop("dibuat")

    class Conn:
        def __init__(self):
            self.log = []

        async def fetchrow(self, sql, *a):
            return {"id": uuid.uuid4(), "nama": "Warung"}

        async def fetch(self, sql, *a):
            self.log.append((sql, a))
            if "FROM journal_entries WHERE tenant_id = $1 AND id = ANY" in sql:
                return [{"id": jid, "created_at": dt.datetime(2026, 11, 9, 8, tzinfo=WIB)}]
            return baris
    c = Conn()
    d = asyncio.run(KM.mutasi(c, "pt-kita-nyawit-bersama", "customer", uuid.uuid4()))
    sql, a = c.log[1]
    assert a == ("pt-kita-nyawit-bersama", [jid]) and d["saldo_akhir"] == 10


# ---------------------------------------------------------------- C
def test_teks_ubah_satu_penentu():
    assert KDT.teks_ubah("address", "Alamat", "jl. Sam Ratulangi") == "Alamat diubah"
    assert KDT.teks_ubah("is_active", "Status aktif", False) == "Dinonaktifkan"
    assert KDT.teks_ubah("is_active", "Status aktif", "True") == "Diaktifkan"


def test_baris_lama_pelanggan_diterjemahkan():
    lama = {"description": "Pelanggan diperbarui", "details": "Alamat: - -> jl. Pierre Tendean\nTermin pembayaran: 0 -> 14\nStatus aktif: True -> False",
            "total": None, "field_name": None, "new_value": None}
    assert KR.ringkas(lama) == "Alamat diubah, Termin pembayaran diubah, Dinonaktifkan"
    gabung = {"description": "Merged customer Toko Sembako (8c1f2a4e-1111-2222-3333-444455556666) into this customer", "details": None,
              "total": None, "field_name": None, "new_value": None}
    assert KR.ringkas(gabung) == "Pelanggan Toko Sembako digabung ke pelanggan ini"
    status_v = {"description": "Status aktif diubah", "details": None, "total": None, "field_name": "is_active", "new_value": "False"}
    assert KR.ringkas(status_v) == "Dinonaktifkan"
    biasa = {"description": "Faktur penjualan INV-1", "details": "Total: 1000", "total": Decimal(1000), "field_name": None, "new_value": None}
    assert KR.ringkas(biasa).startswith("Faktur penjualan INV-1 · Rp")


def test_penulis_pelanggan_seragam_vendor():
    src = (APP / "routers/customers.py").read_text()
    assert "Merged customer" not in src and "'Pelanggan diperbarui'" not in src
    assert src.count("KDT.teks_ubah(") == 1 and "digabung ke pelanggan ini" in src and "Digabung ke pelanggan " in src
    assert "KDT.teks_ubah(" in (APP / "routers/vendors.py").read_text()
