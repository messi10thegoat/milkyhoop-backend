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
    def __init__(self, pf, faktur):
        self.pf, self.faktur, self.q = pf, faktur, []

    async def fetch(self, q, *a):
        assert {int(x) for x in re.findall(r"\$(\d+)", q)} == set(range(1, len(a) + 1)), q
        assert a[0] == T and "tenant_id = $1" in q
        self.q.append(q)
        if "FROM proformas" in q:
            return [p for p in self.pf if p["sales_order_id"] in set(a[1])]
        if "FROM sales_invoices" in q:
            return [{"so_id": s, "n": n} for s, n in self.faktur.items() if s in set(a[1])]
        raise AssertionError(q)


def _pf(so, purpose, status="issued", amount="100", jam=1):
    return {"id": uuid.uuid4(), "sales_order_id": so, "proforma_number": f"PRO-{jam}", "purpose": purpose,
            "amount": D(amount), "status": status, "issued_at": datetime(2026, 10, jam, tzinfo=timezone.utc)}


def test_id_menunggu_dp(monkeypatch):
    s = {k: uuid.uuid4() for k in ("dp", "lunasdp", "termin", "tanpa", "draf_pf", "habis")}
    pf = [_pf(s["dp"], "DP"), _pf(s["lunasdp"], "DP"), _pf(s["termin"], "TERMIN", jam=1), _pf(s["termin"], "DP", jam=2),
          _pf(s["draf_pf"], "DP", status="draft"), _pf(s["habis"], "DP")]
    lunas = pf[1]["id"]
    minta = {}

    async def rg(conn, t, ids):
        minta["rg"] = set(ids)
        return {i: {"tertutup": D("1000") if i == s["habis"] else D("0")} for i in ids}

    async def tb(conn, t, ids):
        return {lunas: {"paid": D("100")}}
    monkeypatch.setattr(SP, "ringkasan_pesanan", rg)
    monkeypatch.setattr(SP, "terbayar_proforma", tb)
    rows = [{"id": i, "status": "confirmed", "total_amount": D("1000")} for i in s.values()]
    c = _C(pf, {})
    hasil = asyncio.run(SP.id_menunggu_dp(c, T, rows))
    assert hasil == {s["dp"]}
    assert minta["rg"] == {s["dp"], s["lunasdp"], s["termin"], s["habis"]}   # hanya SO ber-proforma DP issued


def test_id_menunggu_dp_tanpa_proforma_dp_nol_kueri_uang(monkeypatch):
    async def jangan(*a):
        raise AssertionError("tak boleh menghitung ringkasan tanpa proforma DP issued")
    monkeypatch.setattr(SP, "ringkasan_pesanan", jangan)
    monkeypatch.setattr(SP, "terbayar_proforma", jangan)
    so = uuid.uuid4()
    c = _C([_pf(so, "PELUNASAN")], {})
    assert asyncio.run(SP.id_menunggu_dp(c, T, [{"id": so, "status": "confirmed", "total_amount": D("5")}])) == set()
    assert asyncio.run(SP.id_menunggu_dp(c, T, [])) == set()
