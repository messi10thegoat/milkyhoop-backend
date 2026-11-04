"""Pembulatan rupiah per tenant (putusan pemilik LANGSUNG 4 Nov 2026; pola Accurate digit desimal IDR = 0).

Tiap nominal dokumen dibulatkan di titik lahir (services/uang_presisi.bulat) lewat kalkulator bersama; header = jumlah komponen
(tanpa akun pembulatan). Presisi 2 (flag uang_bersen / bawaan fungsi murni) == perilaku lama PERSIS. Dokumen turunan / ubah dari
sumber tersimpan bersen mewarisi 2 desimal."""
import ast
import asyncio
import random
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import sales_doc_calc as C
from app.services import uang_presisi as UP

D = Decimal
APP = Path(UP.__file__).resolve().parents[1]


@pytest.mark.parametrize("v,p,h", [("1357.95", 0, "1358"), ("1357.49", 0, "1357"), ("0.5", 0, "1"), ("1357.955", 2, "1357.96"),
                                   (None, 0, "0")])
def test_bulat(v, p, h):
    assert UP.bulat(v, p) == D(h)


def _baris(qty, harga, tarif=11, **kw):
    return {"quantity": qty, "unit_price": harga, "tax_rate": tarif, **kw}


def test_presisi_0_rupiah_penuh_dan_menjumlah_persis():
    doc = C.compute_document([_baris(1, "12345"), _baris(3, "999.5", discount_percent=7)], doc_discount_percent="2.5", presisi=0)
    nominal = [doc[k] for k in ("net_subtotal", "doc_discount", "tax_amount", "total_amount", "dpp_total")]
    for ln in doc["items"]:
        nominal += [ln[k] for k in ("subtotal", "net", "doc_discount_allocated", "dpp", "tax_amount", "total")]
    assert all(n == n.to_integral_value() for n in nominal), nominal
    assert doc["total_amount"] == sum((ln["net"] for ln in doc["items"]), D(0)) - doc["doc_discount"] + doc["tax_amount"]
    assert sum((ln["doc_discount_allocated"] for ln in doc["items"]), D(0)) == doc["doc_discount"]
    # baris 1: alokasi diskon dok 2,5% x 15.134 = 378 -> 378 x 12.345/15.134 = 308; (12.345 - 308) x 11% = 1.324,07 -> 1.324
    assert (doc["doc_discount"], doc["items"][0]["doc_discount_allocated"], doc["items"][0]["tax_amount"]) == (D("378"), D("308"), D("1324"))


def test_ppn_satu_baris_contoh_buku():
    doc = C.compute_document([_baris(1, "12345")], presisi=0)
    assert (doc["tax_amount"], doc["total_amount"]) == (D("1358"), D("13703"))  # 1.357,95 -> 1.358
    assert C.compute_document([_baris(1, "12345")])["tax_amount"] == D("1357.95")  # presisi 2 = lama


def test_dpp_nilai_lain_11_12_presisi_0():
    doc = C.compute_document([_baris(1, "100000", 12, dpp_factor_num=11, dpp_factor_den=12)], presisi=0)
    assert (doc["items"][0]["dpp"], doc["tax_amount"]) == (D("91667"), D("11000"))


def test_presisi_2_setara_dengan_lama_acak():
    rnd = random.Random(20261104)
    for _ in range(300):
        items = [_baris(rnd.choice([1, 2, 3, D("1.5")]), D(rnd.randint(100, 999999)) / rnd.choice([1, 100]),
                        rnd.choice([0, 11, 12]), discount_percent=rnd.choice([0, 0, 5, D("7.5")]))
                 for _ in range(rnd.randint(1, 4))]
        kw = {"doc_discount_percent": rnd.choice([0, 0, 3, D("2.5")]), "shipping_amount": rnd.choice([0, 0, 15000, D("1234.5")])}
        assert C.compute_document([dict(i) for i in items], **kw) == C.compute_document([dict(i) for i in items], presisi=2, **kw)


def test_plan_so_invoice_presisi_diteruskan():
    so = [{"id": "a", **_baris(3, "1000.5")}, {"id": "b", **_baris(1, "333")}]
    p0 = C.plan_so_invoice(so, {"a": 1}, so_discount="100", so_shipping=0, presisi=0)
    assert all(ln["net"] == ln["net"].to_integral_value() for ln in p0["items"]) and p0["total_amount"] == p0["total_amount"].to_integral_value()


class _Conn:
    def __init__(self, fitur=(), row=None):
        self.fitur, self.row = list(fitur), row

    async def fetch(self, sql, *a):
        return [{"feature": f} for f in self.fitur]

    async def fetchrow(self, sql, *a):
        return self.row


def test_presisi_tenant_dan_dokumen_turunan():
    run = asyncio.run
    assert run(UP.presisi_tenant(_Conn(), "t")) == 0
    assert run(UP.presisi_tenant(_Conn(["uang_bersen"]), "t")) == 2
    assert run(UP.presisi_dokumen(_Conn(), "t")) == 0
    assert run(UP.presisi_dokumen(_Conn(), "t", {"total_amount": D("13702.95")})) == 2   # sumber lama bersen -> 2
    assert run(UP.presisi_dokumen(_Conn(), "t", {"total_amount": D("13703.00")})) == 0
    assert run(UP.presisi_dari(_Conn(row={"tax_amount": D("0.50")}), "t", "sales_invoices", "x")) == 2
    with pytest.raises(ValueError):
        run(UP.presisi_dari(_Conn(), "t", "users", "x"))


# ── gerbang: SETIAP pemanggil kalkulator di router meneruskan presisi ──────────────────────────────────────────

FUNGSI = {"compute_document", "line_net", "plan_so_invoice", "_so_doc"}
BERKAS = ("routers/sales_invoices.py", "routers/sales_orders.py", "routers/quotes.py", "routers/credit_notes.py")


def test_semua_pemanggil_router_meneruskan_presisi():
    tanpa = []
    for rel in BERKAS:
        for n in ast.walk(ast.parse((APP / rel).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call):
                nama = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else None)
                if nama in FUNGSI and not any(k.arg == "presisi" for k in n.keywords) and len(n.args) < (2 if nama == "line_net" else 99):
                    tanpa.append(f"{rel}:{n.lineno} {nama}")
    assert tanpa == [], tanpa


def test_kontrol_merah_pemeriksa_menangkap_panggilan_tanpa_presisi():
    src = "compute_document(items)\nline_net(it)\n"
    ketemu = [n.lineno for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
              and not any(k.arg == "presisi" for k in n.keywords) and len(n.args) < (2 if n.func.id == "line_net" else 99)]
    assert ketemu == [1, 2]


# ── nominal pembayaran bersen pada presisi 0 (putusan pemilik LANGSUNG 5 Nov 2026) ─────────────────────────────────

def test_tolak_bersen_dan_pengecualian_sisa_lama_persis():
    t = UP.tolak_bersen
    assert t(2, [D("1000.50")]) is None                                  # tenant uang_bersen: bebas
    assert t(0, [D("1000"), D("0"), None]) is None                       # rupiah penuh
    assert t(0, [D("1000.50")]) == "Nominal harus rupiah penuh."        # ketikan bersen ditolak
    # melunasi sisa lama bersen PERSIS: alokasi = sisa -> boleh; total = jumlah alokasi persis -> boleh
    assert t(0, [D("1274.50"), D("274.50"), D("1000")], [(D("274.50"), D("274.50")), (D("1000"), D("5000"))]) is None
    assert t(0, [D("274.25")], [(D("274.25"), D("274.50"))]) == "Nominal harus rupiah penuh."  # bersen tapi BUKAN sisa persis
    assert t(0, [D("1274.75"), D("274.50"), D("1000")], [(D("274.50"), D("274.50")), (D("1000"), D("5000"))]) is not None


def test_tiga_penentu_bayar_terima_memakai_pemeriksa_sama():
    import inspect as _i
    from app.services import bayar_keluar_rencana as BKR
    from app.routers import receive_payments as RP
    for fn in (BKR.rencana_bayar, BKR.rencana_bayar_tagihan, RP._periksa_penerimaan):
        s = _i.getsource(fn)
        assert "UP.tolak_bersen(await UP.presisi_tenant(" in s and "KODE_NOMINAL_RUPIAH" in s, fn.__name__
