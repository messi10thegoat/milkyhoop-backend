"""Ubah SO terkonfirmasi: min_quantity baris = max(terfaktur HIDUP, terkirim) + draf (aturan pemilik). Cacat 5 Okt:
kolom quantity_invoiced SUDAH memuat qty faktur DRAF bertaut, lalu draf_qty ditambah lagi -> draf terhitung DUA KALI
(kaos SO-2609-0166 qty 63 -> min 126; SO-2609-0324 qty 4 -> min 4). Arah salah = kunci berlebihan (aman), kini tepat."""
import asyncio
import random
from decimal import Decimal as D

import pytest

from app.services import so_kirim, so_ubah_terkonfirmasi as UT

SOI = "soi-1"


class _Konn:
    def __init__(self, kolom, hidup, draf):
        self.kolom, self.hidup, self.draf = kolom, hidup, draf

    async def fetch(self, sql, *a):
        if "quantity_invoiced" in sql and "tertaut" in sql:
            return [{"id": SOI, "quantity_invoiced": D(str(self.kolom)), "tertaut": D(str(self.hidup))}]
        if "si.status = 'draft'" in sql:
            return ([{"soi_id": SOI, "id": "inv-d", "invoice_number": "INV-D", "quantity": D(str(self.draf))}]
                    if self.draf else [])
        return []


def _min(monkeypatch, kolom, hidup, draf, kirim=0):
    async def _kirim(conn, tid, ids):
        return ({SOI: D(str(kirim))} if kirim else {}), {}
    monkeypatch.setattr(so_kirim, "terkirim_per_baris", _kirim)
    p = asyncio.run(UT.pemakaian_baris(_Konn(kolom, hidup, draf), "t", "so"))["baris"][SOI]
    return p["terpakai"] + p["draf_qty"]


@pytest.mark.parametrize("nama,kolom,hidup,draf,kirim,harap", [
    ("SO-2609-0166 hanya draf", 63, 0, 63, 0, 63),
    ("SO-2609-0324 hanya draf", 2, 0, 2, 0, 2),
    ("campuran hidup+draf baris sama", 5, 3, 2, 0, 5),
    ("draf/faktur lama TAK bertaut tetap memagari", 4, 0, 0, 0, 4),
    ("terkirim > terfaktur", 1, 1, 1, 3, 4),
    ("tak terpakai", 0, 0, 0, 0, 0),
])
def test_fixture(monkeypatch, nama, kolom, hidup, draf, kirim, harap):
    assert _min(monkeypatch, kolom, hidup, draf, kirim) == D(harap), nama


def test_properti_invarian(monkeypatch):
    acak = random.Random(20261005)
    for _ in range(400):
        h, d, u, k = (acak.randint(0, 20) for _ in range(4))   # hidup bertaut, draf bertaut, lama tak bertaut, terkirim
        kolom = h + d + u
        m = _min(monkeypatch, kolom, h, d, k)
        assert m == D(max(h + u, k) + d), (h, d, u, k, m)       # rumus aturan pemilik, draf SEKALI
        assert m >= D(max(h, k) + d)                            # tak pernah di bawah pemakaian nyata (pagar uang)
        assert m <= D(max(kolom, k) + d)                        # tak pernah lebih ketat dari kolom+draf (cacat lama)
