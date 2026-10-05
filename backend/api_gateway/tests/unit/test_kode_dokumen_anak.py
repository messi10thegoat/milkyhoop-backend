"""Kode order di dokumen anak + label riwayat (3 Okt 2026, MASTER GO). Helper batch: TIGA kueri tetap berapa pun
jumlah dokumennya (tanpa N+1); tenant eksplisit di KEDUA sisi tiap join; medan aditif di detail + list 6 jenis."""
import inspect
import re
import uuid

import pytest

from app.services import kode_order as KO
from app.services.so_riwayat import label_medan

T = "t-uji"


class _Conn:
    def __init__(self, pasangan, so):
        self.pasangan, self.so, self.kueri = pasangan, so, []

    async def fetchrow(self, q, *a):
        self.kueri.append(q)
        assert "order_code_settings" in q and a[0] == T
        return {"enabled": True, "template": "{SEQ}", "min_digits": 3, "reset": "never", "trigger": "so_confirmed",
                "allow_override": True, "label": "Kode order", "title_label": "Judul order"}

    async def fetch(self, q, *a):
        self.kueri.append(q)
        assert a[0] == T, "tenant WAJIB parameter pertama"
        if "FROM sales_orders" in q:
            return [s for s in self.so if s["id"] in a[1]]
        return [p for p in self.pasangan if p["id"] in a[1]]


def _so(kode, judul="KEMEJA GMIM", nomor="SO-1"):
    return {"id": uuid.uuid4(), "order_code": kode, "order_title": judul, "order_number": nomor}


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [1, 50])
async def test_tiga_kueri_tetap_tanpa_n_plus_1(n):
    s = _so("005-10-26")
    ids = [uuid.uuid4() for _ in range(n)]
    c = _Conn([{"id": i, "so_id": s["id"]} for i in ids], [s])
    h = await KO.kode_untuk_dokumen(c, T, "sales_invoice", ids)
    assert len(c.kueri) == 3, len(c.kueri)
    assert all(h[str(i)] == {"order_code": "005-10-26", "order_title": "KEMEJA GMIM", "order_code_label": "Kode order", "order_title_label": "Judul order"}
               for i in ids)


@pytest.mark.asyncio
async def test_tanpa_so_atau_tanpa_kode_null_label_tetap():
    s = _so(None)
    a, b = uuid.uuid4(), uuid.uuid4()
    c = _Conn([{"id": a, "so_id": s["id"]}, {"id": b, "so_id": None}], [s])
    h = await KO.kode_untuk_dokumen(c, T, "proforma", [a, b])
    assert h[str(a)] == h[str(b)] == {"order_code": None, "order_title": None, "order_code_label": "Kode order", "order_title_label": "Judul order"}


@pytest.mark.asyncio
async def test_penerimaan_banyak_so_daftar_terurut():
    s1, s2, s3 = _so("009-10-26", nomor="SO-9"), _so("002-10-26", "KAOS", "SO-2"), _so(None)
    p = uuid.uuid4()
    c = _Conn([{"id": p, "so_id": s1["id"]}, {"id": p, "so_id": s2["id"]}, {"id": p, "so_id": s3["id"]}], [s1, s2, s3])
    h = await KO.kode_untuk_dokumen(c, T, "receive_payment", [p])
    assert h[str(p)] == {"order_code_label": "Kode order", "order_title_label": "Judul order", "order_codes": [
        {"order_code": "002-10-26", "order_title": "KAOS", "order_number": "SO-2"},
        {"order_code": "009-10-26", "order_title": "KEMEJA GMIM", "order_number": "SO-9"}]}


@pytest.mark.asyncio
async def test_tempel_kode_id_bukan_dokumen_jenis_ini_kosong():
    """Penerimaan berinduk JURNAL (id bukan receive_payments) -> nilai kosong, bukan galat."""
    c = _Conn([], [])
    d = [{"id": str(uuid.uuid4())}, {"id": "bukan-uuid"}]
    await KO.tempel_kode(c, T, "receive_payment", d)
    assert d[0]["order_codes"] == [] and d[1]["order_codes"] == [] and d[1]["order_code_label"] == "Kode order"


def test_tenant_eksplisit_di_kedua_sisi_setiap_join():
    for jenis, sql in KO.SUMBER_SO.items():
        assert "tenant_id = $1" in sql, jenis
        for alias, on in re.findall(r"JOIN \w+ (\w+) ON (.*?)(?=\n|$)", sql):
            assert f"{alias}.tenant_id" in on, f"{jenis}: JOIN {alias} tanpa filter tenant: {on}"
    assert "tenant_id = $1" in inspect.getsource(KO.kode_untuk_dokumen).split("FROM sales_orders")[1][:80]


def test_label_riwayat_manusia():
    assert label_medan(["discount_value", "dp_percent", "notes", "subject"]) == "perihal, diskon, uang muka, catatan"
    assert label_medan(["payment_bank_name", "payment_account_number", "items"]) == "barang, rekening"
    assert label_medan(["customer_id", "notes", "medan_baru"]) == "pelanggan, catatan, medan_baru"  # tak dikenal: asli


def test_riwayat_merender_label_saat_baca():
    from app.services import so_riwayat as R
    src = inspect.getsource(R._selesaikan)
    assert 'label_medan(_medan)' in src and '.endswith("_UPDATED")' in src


@pytest.mark.parametrize("modul,fungsi,jenis", [
    ("proformas", "list_proformas", "proforma"), ("proformas", "get_proforma_detail", "proforma"),
    ("sales_invoices", "list_invoices", "sales_invoice"), ("sales_invoices", "get_invoice", "sales_invoice"),
    ("deliveries", "list_deliveries", "delivery"), ("deliveries", "get_delivery_detail", "delivery"),
    ("customer_deposits", "list_customer_deposits", "customer_deposit"),
    ("customer_deposits", "get_customer_deposit", "customer_deposit"),
    ("credit_notes", "list_credit_notes", "credit_note"), ("credit_notes", "get_credit_note", "credit_note"),
    ("receive_payments", "list_receive_payments", "receive_payment"),
    ("receive_payments", "get_receive_payment", "receive_payment"),
])
def test_tiap_rute_menempel_kode(modul, fungsi, jenis):
    import importlib
    m = importlib.import_module(f"app.routers.{modul}")
    assert f'_tempel_kode(' in inspect.getsource(getattr(m, fungsi)) and f'"{jenis}"' in inspect.getsource(getattr(m, fungsi))


def test_skema_mendeklarasikan_medan():
    from app.schemas import credit_notes as CN, customer_deposits as CD, receive_payments as RP, sales_invoices as SI
    for k in (SI.InvoiceListItem, CD.CustomerDepositListItem, CD.CustomerDepositDetail, CN.CreditNoteListItem,
              CN.CreditNoteDetail):
        assert {"order_code", "order_title", "order_code_label"} <= set(k.model_fields), k
    for k in (RP.ReceivePaymentListItem, RP.ReceivePaymentDetail):
        assert {"order_codes", "order_code_label"} <= set(k.model_fields), k


@pytest.mark.asyncio
async def test_tempel_kode_daftar_50_baris_tetap_tiga_kueri():
    s = _so("005-10-26")
    ids = [uuid.uuid4() for _ in range(50)]
    c = _Conn([{"id": i, "so_id": s["id"]} for i in ids], [s])
    d = [{"id": str(i)} for i in ids]
    await KO.tempel_kode(c, T, "credit_note", d)
    assert len(c.kueri) == 3, len(c.kueri)
    assert all(x["order_code"] == "005-10-26" for x in d)
