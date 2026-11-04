"""B1 Pelanggan + Vendor (4 Nov 2026): SATU penentu services/kontak_daftar untuk daftar, ?aksi=, ringkasan, dan aksi baris.
Saldo / lewat tempo dari fungsi jurnal (Decimal, tanpa int); tanpa parameter baru = perilaku lama (+ medan aditif)."""
import asyncio
import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.routers import customers as CU
from app.routers import vendors as VE
from app.services import kontak_daftar as KD

D = Decimal
H = date(2026, 11, 4)


def test_aksi_baris_sama_dengan_predikat():
    assert KD.aksi_baris("customer", {"saldo": D("10"), "lewat_jumlah": 1}) == ["lewat_jatuh_tempo", "punya_piutang"]
    assert KD.aksi_baris("vendor", {"saldo": D("0"), "lewat_jumlah": 0}) == []
    assert KD.aksi_baris("vendor", {"saldo": D("5"), "lewat_jumlah": 0}) == ["punya_hutang"]
    assert KD.sql_aksi("customer", "punya_piutang") == "COALESCE(x.saldo, 0) > 0"
    assert KD.sql_aksi("vendor", "lewat_jatuh_tempo") == "COALESCE(x.lewat_jumlah, 0) > 0"
    with pytest.raises(ValueError):
        KD.sql_aksi("customer", "punya_hutang")  # kunci vendor di pelanggan = asing


def test_cte_dari_fungsi_jurnal_dan_lewat_tempo_berdokumen():
    c, v = KD.cte_fakta("customer", "$3"), KD.cte_fakta("vendor", "$3")
    assert "compute_ar_outstanding($1)" in c and "customer_id::text AS pihak" in c and "invoice_id IS NOT NULL" in c
    assert "compute_ap_outstanding($1)" in v and "vendor_id::text AS pihak" in v and "bill_id IS NOT NULL" in v
    assert "due_date < $3::date" in c and "outstanding > 0" in c


@pytest.mark.parametrize("tel,kota,harap", [("081243065632", "Manado", "0812 4306 5632 · Manado"), (None, "Bitung", "Bitung"),
                                            ("", "", None), ("+6281243065632", None, "+628 1243 0656 32")])
def test_kontak_teks(tel, kota, harap):
    assert KD.kontak_teks(tel, kota) == harap


class _Conn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows


def test_fakta_dan_medan_baris_sen_terjaga():
    c = _Conn([{"pihak": "a", "saldo": D("274.50"), "lewat_nilai": D("100.25"), "lewat_jumlah": 1}])
    f = asyncio.run(KD.fakta_kontak(c, "t", "customer", ["a", "b"], H))
    assert c.sql[0][1] == ("t", ["a", "b"], H)
    m = KD.medan_baris("customer", {"id": "a", "telepon": "081243065632", "city": "Manado", "is_active": True}, f)
    assert (m["saldo"], m["lewat_tempo_nilai"], m["lewat_tempo_jumlah"]) == (274.5, 100.25, 1)
    assert m["aksi"] == "lewat_jatuh_tempo" and m["aksi_semua"] == ["lewat_jatuh_tempo", "punya_piutang"] and m["status_text"] == "Aktif"
    kosong = KD.medan_baris("customer", {"id": "b", "telepon": None, "city": None, "is_active": False}, f)
    assert (kosong["saldo"], kosong["aksi"], kosong["aksi_semua"], kosong["status_text"], type(kosong["saldo"])) == (0, None, [], "Nonaktif", int)


def test_ringkasan_dan_halaman_memakai_predikat_sama():
    for fn in (KD.ringkasan, KD.halaman):
        s = inspect.getsource(fn)
        assert "sql_aksi(jenis" in s and "cte_fakta(jenis" in s, fn.__name__


def test_router_satu_pembangun_syarat_dan_tanpa_int_saldo():
    for fn, syarat in ((CU.list_customers, "_syarat_pelanggan("), (CU.customers_summary, "_syarat_pelanggan("),
                       (VE.list_vendors, "_syarat_vendor("), (VE.vendors_summary, "_syarat_vendor(")):
        assert syarat in inspect.getsource(fn), fn.__name__
    cu, ve = inspect.getsource(CU.list_customers), inspect.getsource(VE.list_vendors)
    assert 'int(row["balance"])' not in cu and "KD.fakta_kontak(" in cu and "KD.halaman(" in cu
    assert "opening_balance" not in inspect.getsource(VE._syarat_vendor) and '"punya_hutang" if has_balance is True' in ve


def test_summary_terdaftar_sebelum_id():
    for mod, id_path in ((CU, "/{customer_id}"), (VE, "/{vendor_id}")):
        paths = [getattr(r, "path", "") for r in mod.router.routes]
        assert paths.index("/summary") < paths.index(id_path), mod.__name__


def test_skema_respons_membawa_medan_baru_dan_angka_bersen():
    from app.schemas.customers import CustomerListItem, CustomerListResponse
    from app.schemas.vendors import VendorListItem, VendorListResponse
    for m in (CustomerListItem, VendorListItem):
        for k in ("saldo", "lewat_tempo_nilai", "lewat_tempo_jumlah", "kontak_teks", "status_text", "aksi", "aksi_semua"):
            assert k in m.model_fields, (m.__name__, k)
    assert "sum_sisa" in CustomerListResponse.model_fields and "sum_sisa" in VendorListResponse.model_fields
    it = CustomerListItem(id="a", name="Warung", outstanding_balance=D("274.50"), saldo=D("274.50"))
    assert it.outstanding_balance == 274.5 and isinstance(it.outstanding_balance, float)  # tetap JSON number (FE typeof)
