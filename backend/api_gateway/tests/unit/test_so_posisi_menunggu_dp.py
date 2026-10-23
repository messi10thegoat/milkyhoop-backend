"""AKSI-SO-2 (14 Okt 2026): so_posisi.menunggu_dp = SATU fungsi murni di balik Posisi "Menunggu DP" DAN pill
so_aksi menunggu_dp; id_menunggu_dp mengumpulkan fakta yang sama dengan fakta_daftar (koneksi tiruan KETAT)."""
import asyncio
import inspect
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from app.services import so_posisi as SP

DP = {"purpose": "DP", "termin_ke": None}
PEL = {"purpose": "PELUNASAN", "termin_ke": None}
TER = {"purpose": "TERMIN", "termin_ke": 1}


@pytest.mark.parametrize("st,sisa,bb,fak,harap", [
    ("confirmed", D("10"), [DP], False, True),
    ("invoiced", D("10"), [DP], True, True),
    ("confirmed", D("10"), [TER, DP], False, False),       # termin pertama -> "Menunggu termin 1"
    ("confirmed", D("10"), [PEL, DP], True, True),          # PELUNASAN dilewati bila faktur ada (#3)
    ("confirmed", D("10"), [PEL, DP], False, False),        # tanpa faktur PELUNASAN berlaku -> "Menunggu tagihan"
    ("confirmed", D("0"), [DP], False, False),              # lunas
    ("cancelled", D("10"), [DP], False, False),
    ("confirmed", D("10"), [], False, False),
])
def test_menunggu_dp_sama_dengan_teks_posisi(st, sisa, bb, fak, harap):
    assert SP.menunggu_dp(st, sisa, bb, fak) is harap
    teks, _ = SP.posisi(st, sisa, bb, fak, D("0"), False, D("0"))
    assert (teks == "Menunggu DP") is harap


def test_fase_mengganti_teks_bukan_fakta():
    assert SP.posisi("confirmed", D("10"), [DP], False, D("0"), False, D("0"), "Tersedia di Toko")[0] == "Tersedia di Toko"
    assert SP.menunggu_dp("confirmed", D("10"), [DP], False) is True


def test_posisi_memanggil_fungsi_yang_sama():
    assert "menunggu_dp(status, sisa, belum_bayar, ada_faktur)" in inspect.getsource(SP.posisi)
    src = inspect.getsource(SP.fakta_daftar)
    assert "belum_bayar_so(daftar_pf, terbayar)" in src and "SQL_PROFORMA_SO" in src and "SQL_JUMLAH_FAKTUR" in src


T = "kaos-biru-konveksi"


class _C:
    def __init__(self, pf, faktur, deposit=()):
        self.pf, self.faktur, self.deposit, self.q = pf, faktur, set(deposit), []

    async def fetch(self, q, *a):
        assert {int(x) for x in re.findall(r"\$(\d+)", q)} == set(range(1, len(a) + 1)), q
        assert a[0] == T and "tenant_id = $1" in q
        self.q.append(q)
        if "FROM proformas" in q:
            return [p for p in self.pf if p["sales_order_id"] in set(a[1])]
        if "FROM sales_invoices" in q:
            return [{"so_id": s, "n": n} for s, n in self.faktur.items() if s in set(a[1])]
        if "FROM customer_deposits" in q:
            assert set(a[2]) == {"void", "draft", "refunded"}
            return [{"so_id": s} for s in self.deposit if s in set(a[1])]
        raise AssertionError(q)


def _pf(so, purpose, status="issued", amount="100", jam=1):
    return {"id": uuid.uuid4(), "sales_order_id": so, "proforma_number": f"PRO-{jam}", "purpose": purpose,
            "amount": D(amount), "status": status, "issued_at": datetime(2026, 10, jam, tzinfo=timezone.utc)}


def _jalan(monkeypatch, s, pf, rows, faktur=None, deposit=(), dibayar=None, tutup=()):
    minta = {}

    async def rg(conn, t, ids):
        minta["rg"] = set(ids)
        return {i: {"tertutup": D("1000") if i in tutup else D("0")} for i in ids}

    async def tb(conn, t, ids):
        minta["tb"] = set(ids)
        return dibayar or {}
    monkeypatch.setattr(SP, "ringkasan_pesanan", rg)
    monkeypatch.setattr(SP, "terbayar_proforma", tb)
    return asyncio.run(SP.fakta_dp(_C(pf, faktur or {}, deposit), T, rows)), minta


def _so(i, term=True, status="confirmed", dp_amount=None):
    return {"id": i, "status": status, "total_amount": D("1000"), "dp_percent": D("30") if term else None,
            "dp_amount": dp_amount}


def test_fakta_dp_menunggu(monkeypatch):
    s = {k: uuid.uuid4() for k in ("dp", "lunasdp", "termin", "habis", "sebagian")}
    pf = [_pf(s["dp"], "DP"), _pf(s["lunasdp"], "DP"), _pf(s["termin"], "TERMIN", jam=1), _pf(s["termin"], "DP", jam=2),
          _pf(s["habis"], "DP"), _pf(s["sebagian"], "DP", amount="150")]
    bayar = {pf[1]["id"]: {"paid": D("100")}, pf[5]["id"]: {"paid": D("10")}}
    rows = [_so(i, term=False) for i in s.values()]
    hasil, minta = _jalan(monkeypatch, s, pf, rows, dibayar=bayar, tutup={s["habis"]})
    # MENUNGGU-PEMBAYARAN (23 Okt): proforma terbit belum lunas APA PUN jenisnya; SO termin (TERMIN + DP belum dibayar) kini ikut, nilai Σ
    assert hasil == {s["dp"]: ("menunggu_pembayaran", D("100")), s["termin"]: ("menunggu_pembayaran", D("200")),
                     s["sebagian"]: ("menunggu_pembayaran", D("140"))}
    assert minta["tb"] == {s["dp"], s["lunasdp"], s["termin"], s["habis"], s["sebagian"]}


def test_fakta_dp_perlu_minta(monkeypatch):
    k = ("minta", "minta_nominal", "pelunasan", "draf_pf", "batal_pf", "deposit", "faktur", "tanpa_term", "habis", "draf_so")
    s = {x: uuid.uuid4() for x in k}
    pf = [_pf(s["pelunasan"], "PELUNASAN"), _pf(s["draf_pf"], "DP", status="draft"),
          _pf(s["batal_pf"], "DP", status="cancelled")]
    rows = [_so(s[x]) for x in k if x not in ("minta_nominal", "tanpa_term", "draf_so")]
    rows += [_so(s["minta_nominal"], dp_amount=D("250")), _so(s["tanpa_term"], term=False),
             _so(s["draf_so"], status="draft")]
    hasil, minta = _jalan(monkeypatch, s, pf, rows, faktur={s["faktur"]: 1}, deposit={s["deposit"]}, tutup={s["habis"]})
    assert hasil == {s["minta"]: ("perlu_minta_dp", D("300.00")), s["minta_nominal"]: ("perlu_minta_dp", D("250")),
                     s["draf_pf"]: ("perlu_minta_dp", D("300.00")), s["batal_pf"]: ("perlu_minta_dp", D("300.00"))}
    assert "tb" not in minta                       # tanpa proforma DP terbit: terbayar tak dihitung


def test_fakta_dp_usaha_tanpa_dp_satu_kueri(monkeypatch):
    async def jangan(*a):
        raise AssertionError("tak boleh menghitung uang tanpa calon DP")
    monkeypatch.setattr(SP, "ringkasan_pesanan", jangan)
    monkeypatch.setattr(SP, "terbayar_proforma", jangan)
    so = uuid.uuid4()
    c = _C([_pf(so, "PELUNASAN")], {})
    assert asyncio.run(SP.fakta_dp(c, T, [_so(so, term=False)])) == {}
    assert len(c.q) == 1
    assert asyncio.run(SP.fakta_dp(c, T, [])) == {}


@pytest.mark.parametrize("args,harap", [
    (("confirmed", True, False, False, False, D("1")), True),
    (("partial_invoiced", True, False, False, False, D("1")), True),
    (("confirmed", False, False, False, False, D("1")), False),
    (("confirmed", True, True, False, False, D("1")), False),
    (("confirmed", True, False, True, False, D("1")), False),
    (("confirmed", True, False, False, True, D("1")), False),
    (("confirmed", True, False, False, False, D("0")), False),
    (("draft", True, False, False, False, D("1")), False),
    (("completed", True, False, False, False, D("1")), False),
])
def test_perlu_minta_dp_murni(args, harap):
    assert SP.perlu_minta_dp(*args) is harap


def test_dp_wajib():
    assert SP.dp_wajib(D("1000"), D("30"), None) == D("300.00")
    assert SP.dp_wajib(D("1000"), D("30"), D("90")) == D("90")
    assert SP.dp_wajib(D("333.33"), D("50"), D("0")) == D("166.67")   # half-up = ROUND SQL kartu lama
    assert SP.punya_term_dp(None, D("0")) is False and SP.punya_term_dp(D("0"), D("5")) is True
