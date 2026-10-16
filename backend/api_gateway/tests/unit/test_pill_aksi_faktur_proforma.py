"""Pill AKSI daftar Faktur & Proforma (14 Okt 2026, W-20261014-AKSI-FAKTUR-PROFORMA; putusan MASTER, pola
NetSuite/Xero). Server menentukan keanggotaan (FE tak menghitung tanggal): ?aksi= di GET /sales-invoices &
GET /proformas, `aksi` per baris, *_count/*_value di /summary -- SATU aturan (services/faktur_aksi, proforma_aksi),
tanggal BISNIS tenant. Kesetaraan Python<->SQL<->daftar<->ringkasan di data nyata: harness
scripts/ops/uji-nyata-aksi-faktur-proforma.py."""
import inspect
import re
import typing
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import proformas as PR
from app.routers import sales_invoices as SI
from app.schemas.sales_invoices import InvoiceListItem, InvoiceSummary
from app.services import dashboard_v2 as D
from app.services import faktur_aksi as FA
from app.services import proforma_aksi as PA

H = date(2026, 10, 14)
TENANT = "kaos-biru-konveksi"
HR = lambda n: H + timedelta(days=n)  # noqa: E731


# ---------------------------------------------------------------- FAKTUR: aturan Python
@pytest.mark.parametrize("status,sisa,due,tgl,harap", [
    ("draft", None, None, HR(-3), "perlu_diterbitkan"),
    ("draft", 0, HR(-30), HR(-60), "perlu_diterbitkan"),          # draf lewat tanggal tetap "perlu diterbitkan"
    ("posted", 100, HR(-1), HR(-30), "jatuh_tempo"),
    ("partial", Decimal("0.01"), HR(-10), HR(-40), "jatuh_tempo"),
    ("posted", 100, H, HR(-30), "segera_tempo"),                  # jatuh tempo HARI INI = segera (belum lewat)
    ("posted", 100, HR(3), HR(-30), "segera_tempo"),              # tepat ambang
    ("posted", 100, HR(4), HR(-30), "belum_lunas"),               # 4 > 3
    ("partial", 100, HR(60), HR(-1), "belum_lunas"),
    ("posted", 100, None, HR(-1), "jatuh_tempo"),                 # due kosong -> invoice_date (due on receipt)
    ("posted", 100, None, H, "segera_tempo"),                     # invoice_date = hari ini
    ("posted", 100, None, HR(1), "segera_tempo"),                 # tgl faktur masa depan <= ambang
    ("posted", 100, None, HR(9), "belum_lunas"),
    ("paid", 0, HR(-30), HR(-60), None),                          # lunas = arsip
    ("posted", 0, HR(-30), HR(-60), None),                        # sisa jurnal 0 walau status belum 'paid'
    ("posted", None, HR(-30), HR(-60), None),                     # tak ada baris jurnal = bukan aksi
    ("void", 100, HR(-30), HR(-60), None),
    ("void", None, None, HR(-60), None),
])
def test_aturan_aksi_faktur(status, sisa, due, tgl, harap):
    assert FA.aksi_faktur(status, sisa, due, tgl, H) == harap


def test_jatuh_tempo_efektif_satu_helper():
    assert FA.jatuh_tempo_efektif(HR(5), HR(-9)) == HR(5)
    assert FA.jatuh_tempo_efektif(None, HR(-9)) == HR(-9)
    assert FA.jatuh_tempo_efektif(None, None) is None
    assert FA.SQL_JATUH_TEMPO_EFEKTIF == "COALESCE(si.due_date, si.invoice_date)"


def test_ambang_bawaan_tiga_dan_bisa_diganti():
    assert FA.AMBANG_SEGERA_TEMPO == 3
    assert FA.aksi_faktur("posted", 1, HR(5), HR(-1), H, ambang=7) == "segera_tempo"


def test_kunci_faktur_saling_eksklusif_dan_gabungan_tiga_sama_dengan_pilih_belum_lunas():
    """belum_lunas + segera_tempo + jatuh_tempo == dashboard_v2.pilih_belum_lunas (?tugas=belum_lunas)."""
    ar = []
    for i, (due, tgl, sisa) in enumerate([(HR(-5), HR(-30), 10), (H, HR(-5), 5), (HR(2), HR(-1), 7), (HR(30), HR(-1), 9),
                                          (None, HR(-2), 3), (None, H, 4), (HR(-1), HR(-9), 0), (HR(-1), HR(-9), -5)]):
        ar.append({"invoice_id": None if sisa < 0 else f"i{i}", "outstanding": sisa, "due_date": due,
                   "invoice_date": tgl, "status": "posted"})
    terbit = {r["invoice_id"] for r in D.pilih_belum_lunas(ar)}
    tiga = {r["invoice_id"] for r in ar
            if FA.aksi_faktur(r["status"], r["outstanding"] if r["invoice_id"] else None, r["due_date"],
                              r["invoice_date"], H) in ("belum_lunas", "segera_tempo", "jatuh_tempo")}
    assert tiga == terbit == {"i0", "i1", "i2", "i3", "i4", "i5"}


def test_telat_lama_dan_jatuh_tempo_berselisih_HANYA_untuk_due_kosong():
    """Dikunci: ?tugas=telat/kartu dashboard memakai aturan lama (due kosong = telat). Penyelarasan = tiket terpisah."""
    for due, tgl in [(HR(-1), HR(-9)), (HR(0), HR(-9)), (HR(5), HR(-9))]:
        telat = D._jatuh_tempo_lewat(due, H)
        assert (FA.aksi_faktur("posted", 1, due, tgl, H) == "jatuh_tempo") is telat
    assert D._jatuh_tempo_lewat(None, H) is True
    assert FA.aksi_faktur("posted", 1, None, H, H) == "segera_tempo"  # selisih yang diketahui


# ---------------------------------------------------------------- FAKTUR: SQL & kontrak
def test_sql_aksi_faktur_terkunci():
    s = {k: FA.sql_aksi(k, "$2", "$3") for k in FA.AKSI}
    eff = "COALESCE(si.due_date, si.invoice_date)"
    terbit = f"(si.status NOT IN ('draft', 'void') AND {FA.SQL_SISA_POSITIF_SUBQUERY})"
    assert set(FA.AKSI) == {"perlu_diterbitkan", "belum_lunas", "segera_tempo", "jatuh_tempo"}
    assert s["perlu_diterbitkan"] == "(si.status = 'draft')"
    assert s["jatuh_tempo"] == f"({terbit} AND {eff} < $2::date)"
    assert s["segera_tempo"] == f"({terbit} AND {eff} >= $2::date AND {eff} <= ($2::date + $3::int))"
    assert s["belum_lunas"] == f"({terbit} AND {eff} > ($2::date + $3::int))"
    assert "compute_ar_outstanding($1)" in FA.SQL_SISA_POSITIF_SUBQUERY
    with pytest.raises(KeyError):
        FA.sql_aksi("tak_ada", "$2", "$3")


@pytest.mark.parametrize("aksi", list(FA.AKSI))
def test_berparam_hanya_parameter_yang_dirujuk(aksi):
    pred, par = FA.sql_aksi_berparam(aksi, 5, H)
    n = sorted({int(x) for x in re.findall(r"\$(\d+)", pred) if int(x) >= 5})
    assert n == list(range(5, 5 + len(par))), (aksi, n, par)
    assert "$1" in pred or aksi == "perlu_diterbitkan"  # tenant tetap $1 (sisa dari compute_ar_outstanding($1))


def test_nilai_ringkasan_draf_total_terbit_sisa():
    assert FA.sql_nilai("perlu_diterbitkan") == "si.total_amount"
    for k in ("belum_lunas", "segera_tempo", "jatuh_tempo"):
        assert FA.sql_nilai(k) == "ar_fn.outstanding"


def _literal_args(fn, nama):
    ann = inspect.signature(fn).parameters[nama].annotation
    return tuple(typing.get_args([a for a in typing.get_args(ann) if a is not type(None)][0]))


def test_param_aksi_daftar_sama_dengan_AKSI():
    # 16 Okt 2026 (listpage P3): + satu kunci TAMBAHAN tak-eksklusif (SI.AKSI_MINGGU_INI); kunci FA.AKSI tetap berurutan sama
    assert _literal_args(SI.list_invoices, "aksi") == FA.AKSI + (SI.AKSI_MINGGU_INI,)
    assert _literal_args(PR.list_proformas, "aksi") == PA.AKSI


def test_skema_faktur_membawa_aksi_dan_ringkasan_delapan_medan():
    assert "aksi" in InvoiceListItem.model_fields  # tanpa ini response_model MEMBUANG medan diam-diam
    f = InvoiceSummary.model_fields
    for k in FA.AKSI:
        assert f"{k}_count" in f and f"{k}_value" in f


# ---------------------------------------------------------------- FAKTUR: ringkasan di atas koneksi palsu
class Conn:
    def __init__(self):
        self.kueri = []

    @staticmethod
    def _cek(sql, a):
        n = {int(x) for x in re.findall(r"\$(\d+)", sql)}
        assert n == set(range(1, len(a) + 1)), (sorted(n), len(a), sql)

    async def fetchrow(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        return {k: 0 for k in re.findall(r"\bas (\w+)", sql, flags=re.I)}


class Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class A:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return A()


@pytest.mark.asyncio
async def test_ringkasan_faktur_empat_aksi_count_value(monkeypatch):
    c = Conn()

    async def gp():
        return Pool(c)

    async def hari(conn, t):
        return H
    monkeypatch.setattr(SI, "get_pool", gp)
    monkeypatch.setattr(SI, "tanggal_dokumen", hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": None, "tenant_id": TENANT}))
    r = await SI.get_invoice_summary(req)
    sql, a = [q for q in c.kueri if "AS perlu_diterbitkan_count" in q[0]][-1]   # kueri pill (chip minggu ini = kueri sendiri)
    assert a == (TENANT, H, FA.AMBANG_SEGERA_TEMPO)
    for k in FA.AKSI:
        pred = FA.sql_aksi(k, "$2", "$3", FA.SQL_SISA_POSITIF_JOIN)
        assert f"COUNT(*) FILTER (WHERE {pred}) AS {k}_count" in sql
        assert f"SUM({FA.sql_nilai(k)}) FILTER (WHERE {pred}), 0) AS {k}_value" in sql
        assert f"{k}_count" in r["data"] and f"{k}_value" in r["data"]
    assert "overdue_count" in r["data"] and "unpaid_count" in r["data"]  # medan lama utuh
    assert "LEFT JOIN ar_fn ON ar_fn.invoice_id = si.id WHERE si.tenant_id = $1" in sql


@pytest.mark.asyncio
async def test_ringkasan_payung_faktur_dan_kredit_nk_terpisah(monkeypatch):
    """MASTER 14 Okt: payung = Σ tiga kunci terbit; kredit NK TERPISAH; payung - kredit == total_outstanding (neto, tak diubah)."""
    class C(Conn):
        async def fetchrow(self, sql, *a):
            r = await super().fetchrow(sql, *a)
            if "total_outstanding" in sql:
                r.update(total_outstanding=Decimal("5765000"), kredit_count=2, kredit_value=Decimal("46000"))
            else:
                r.update(belum_lunas_count=10, belum_lunas_value=Decimal("600000"), segera_tempo_count=2,
                         segera_tempo_value=Decimal("0"), jatuh_tempo_count=5, jatuh_tempo_value=Decimal("5211000"))
            return r
    c = C()

    async def gp():
        return Pool(c)

    async def hari(conn, t):
        return H
    monkeypatch.setattr(SI, "get_pool", gp)
    monkeypatch.setattr(SI, "tanggal_dokumen", hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": None, "tenant_id": TENANT}))
    d = (await SI.get_invoice_summary(req))["data"]
    assert d["faktur_belum_lunas_count"] == 17
    assert d["faktur_belum_lunas_value"] == 5811000
    assert d["faktur_belum_lunas_value"] == sum(d[f"{k}_value"] for k in ("belum_lunas", "segera_tempo", "jatuh_tempo"))
    assert d["kredit_nota_belum_terpakai_count"] == 2 and d["kredit_nota_belum_terpakai_value"] == 46000
    assert d["faktur_belum_lunas_value"] - d["kredit_nota_belum_terpakai_value"] == d["total_outstanding"]
    assert d["total_outstanding"] == 5765000  # neto lama tak diubah (dashboard memakainya)


def test_skema_ringkasan_mendeklarasikan_medan_payung():  # response_model membuang medan tak terdeklarasi diam-diam
    for f in ("faktur_belum_lunas_count", "faktur_belum_lunas_value",
              "kredit_nota_belum_terpakai_count", "kredit_nota_belum_terpakai_value"):
        assert f in InvoiceSummary.model_fields


# ---------------------------------------------------------------- PROFORMA
@pytest.mark.parametrize("status,so,lunas,due,harap", [
    ("draft", "confirmed", False, None, "perlu_diterbitkan"),
    ("issued", "confirmed", False, None, "menunggu_bayar"),        # tanpa jatuh tempo = menunggu
    ("issued", "confirmed", False, HR(30), "menunggu_bayar"),
    ("issued", "confirmed", False, HR(4), "menunggu_bayar"),       # 4 > 3
    ("issued", "confirmed", False, HR(3), "segera_tempo"),
    ("issued", "confirmed", False, H, "segera_tempo"),             # jatuh tempo hari ini belum lewat
    ("issued", "confirmed", False, HR(-1), "lewat_tempo"),
    ("issued", "invoiced", False, HR(-20), "lewat_tempo"),
    ("issued", "confirmed", True, HR(-20), None),                  # terbayar penuh = bukan aksi
    ("issued", "cancelled", False, HR(-20), None),                 # terbit pada SO batal DIKECUALIKAN (putusan MASTER)
    ("issued", "cancelled", False, None, None),
    ("cancelled", "confirmed", False, HR(-1), None),
    ("expired", "confirmed", False, HR(-1), None),
    ("issued", None, False, HR(-1), "lewat_tempo"),                # SO tak terbaca (LEFT JOIN) tak dianggap batal
])
def test_aturan_aksi_proforma(status, so, lunas, due, harap):
    assert PA.aksi_proforma(status, so, lunas, due, H) == harap


def test_proforma_tak_punya_kunci_perlu_dikonversi_dan_berlaku():
    assert PA.AKSI == ("perlu_diterbitkan", "menunggu_bayar", "segera_tempo", "lewat_tempo")  # Law 34: tak dikarang


@pytest.mark.parametrize("nominal,bayar,harap", [
    (1000, 1000, True), (1000, "999.995", True), (1000, "999.99", False), (1000, 0, False), (1000, None, False),
    (Decimal("1000.00"), Decimal("1000.00"), True),
])
def test_rumus_lunas_tunggal(nominal, bayar, harap):
    assert PA.proforma_lunas(nominal, bayar) is harap


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        if "FROM proformas p" in sql:
            return self.rows
        return []  # terbayar_proforma: tak ada SO terkait deposit -> 0 terbayar


@pytest.mark.asyncio
async def test_pemilih_semua_proforma_sama_dengan_aturan_per_baris(monkeypatch):
    def r(i, st, so, amt, due):
        return {"id": f"p{i}", "sales_order_id": f"s{i}", "status": st, "amount": amt, "due_date": due, "so_status": so}
    rows = [r(1, "draft", "confirmed", 100, None), r(2, "issued", "confirmed", 200, HR(-2)),
            r(3, "issued", "confirmed", 300, HR(2)), r(4, "issued", "cancelled", 400, HR(-9)),
            r(5, "issued", "confirmed", 500, None), r(6, "issued", "confirmed", 600, HR(20)),
            r(7, "cancelled", "confirmed", 700, None)]
    pembayaran = {"p2": Decimal("50"), "p6": Decimal("600")}  # p6 lunas

    async def tb(conn, tid, so_ids):
        return {r_["id"]: {"paid": pembayaran.get(r_["id"], Decimal("0")), "paid_breakdown": {}} for r_ in rows}
    monkeypatch.setattr(PR, "terbayar_proforma", tb)
    hasil = await PR.aksi_semua_proforma(_Rows(rows), TENANT, H)
    assert {k: v[0] for k, v in hasil.items()} == {
        "p1": "perlu_diterbitkan", "p2": "lewat_tempo", "p3": "segera_tempo", "p5": "menunggu_bayar"}
    assert hasil["p1"][1] == Decimal("100")      # draf = nominal
    assert hasil["p2"][1] == Decimal("150")      # terbit = SISA (200 - 50)
    # aturan per baris yang dipakai daftar = fungsi murni yang SAMA
    for x in rows:
        lunas = PA.proforma_lunas(x["amount"], pembayaran.get(x["id"], 0))
        a = PA.aksi_proforma(x["status"], x["so_status"], lunas, x["due_date"], H)
        assert (hasil.get(x["id"]) or (None,))[0] == a, x["id"]


def test_kelas_bayar_terbit_memakai_rumus_lunas_tunggal():
    assert "_pa.proforma_lunas(nominal, bayar)" in inspect.getsource(PR.kelas_bayar_terbit)
    assert "0.005" not in inspect.getsource(PR.kelas_bayar_terbit)
