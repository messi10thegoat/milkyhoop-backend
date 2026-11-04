"""BEBAN non-PKP (4 Nov 2026, aturan disetujui pemilik LANGSUNG "Ya, kapitalisasi"): beban ber-PPN tenant non-PKP TIDAK lagi ditolak 422;
PPN DIKAPITALISASI ke baris beban (tanpa Dr PPN Masukan) lewat penentu SAMA dengan tagihan (tagihan_aksi.dasar_tagihan + neto_baris).
Contoh: beban 1.000.000 + PPN 11% -> Dr Beban 1.110.000, Cr Kas 1.110.000. PKP tidak berubah. Pratinjau == penulis (dulu non-PKP timpang).
Tiruan DB mencocokkan POLA kueri."""
import ast
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.routers import expenses as E

APP = Path(E.__file__).resolve().parents[1]
T = "kaos-biru-konveksi"
PPN, KAS, B1, B2 = (uuid.UUID(int=i) for i in (11, 12, 13, 14))


class Kon:
    def __init__(self):
        self.baris = []

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if q.startswith("INSERT INTO journal_entries"):
            return uuid.UUID(int=99)
        return None

    async def fetchrow(self, sql, *a):
        return None

    async def execute(self, sql, *a):
        q = " ".join(sql.split())
        if q.startswith("INSERT INTO journal_lines"):
            self.baris.append((str(a[2]), D(str(a[3])), D("0")) if " 0, $5)" in q else (str(a[2]), D("0"), D(str(a[3]))))
        return "OK"


def _vat(akun):
    async def f(conn, tid, peran):
        return akun
    return f


async def _jurnal(monkeypatch, pkp, item=None, subtotal="1000000", pajak="110000"):
    monkeypatch.setattr(E, "resolve_account_id_by_role_if_pkp", _vat(PPN if pkp else None))
    k = Kon()
    await E.create_expense_journal(k, T, uuid.uuid4(), "EXP-1", date(2026, 11, 4), bool(item), B1, item, D(subtotal), D(pajak), D("0"),
                                   KAS, None)
    return k.baris


@pytest.mark.asyncio
async def test_non_pkp_beban_ppn_dikapitalisasi_tanpa_ppn_masukan(monkeypatch):
    b = await _jurnal(monkeypatch, pkp=False)
    assert b == [(str(B1), D("1110000"), D("0")), (str(KAS), D("0"), D("1110000"))]  # contoh pemilik


@pytest.mark.asyncio
async def test_non_pkp_berbaris_pro_rata_sen_tepat(monkeypatch):
    item = [NS(account_id=B1, amount=333333, notes=None), NS(account_id=B2, amount=666667, notes=None)]
    b = await _jurnal(monkeypatch, pkp=False, item=item)
    dr = [x for x in b if x[1] > 0]
    assert [x[0] for x in dr] == [str(B1), str(B2)] and sum(x[1] for x in dr) == D("1110000") == b[-1][2]
    assert dr[0][1] == D("369999.63")  # 333333 + porsi 110000 x 333333/1000000 (2dp), sisa sen ke baris terakhir


@pytest.mark.asyncio
async def test_pkp_tidak_berubah_ppn_masukan_terpisah(monkeypatch):
    b = await _jurnal(monkeypatch, pkp=True)
    assert b == [(str(B1), D("1000000"), D("0")), (str(PPN), D("110000"), D("0")), (str(KAS), D("0"), D("1110000"))]


@pytest.mark.asyncio
async def test_tanpa_pajak_sama_untuk_keduanya(monkeypatch):
    for pkp in (True, False):
        assert await _jurnal(monkeypatch, pkp=pkp, pajak="0") == [(str(B1), D("1000000"), D("0")), (str(KAS), D("0"), D("1000000"))]


def test_penjaga_tolak_hilang_dan_pratinjau_memakai_penentu_sama():
    src = (APP / "routers/expenses.py").read_text()
    assert "PESAN_NON_PKP_PEMBELIAN" not in src and "VAT line skipped" not in src
    pohon = ast.parse(src)
    for nama in ("create_expense_journal", "preview_journal"):
        fn = next(n for n in ast.walk(pohon) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)
        assert "TA.dasar_tagihan(" in ast.unparse(fn), nama


# ---------------------------------------------------------------- RETUR (ND) non-PKP: kapitalisasi balik (aturan pemilik 4 Nov)
HUTANG, RETUR = uuid.UUID(int=21), uuid.UUID(int=22)


@pytest.mark.asyncio
@pytest.mark.parametrize("pkp,pajak,harap", [
    (False, "11000", [(HUTANG, D("111000"), 0), (RETUR, 0, D("111000"))]),                       # contoh pemilik: tanpa PPN Masukan
    (True, "11000", [(HUTANG, D("111000"), 0), (RETUR, 0, D("100000")), (PPN, 0, D("11000"))]),   # PKP tidak berubah
    (False, "0", [(HUTANG, D("100000"), 0), (RETUR, 0, D("100000"))])])
async def test_nd_non_pkp_retur_termasuk_ppn(monkeypatch, pkp, pajak, harap):
    from app.services import nota_debit_rencana as NDR
    monkeypatch.setattr(NDR, "resolve_account_id_by_role_if_pkp", _vat(PPN if pkp else None))
    total = D("100000") + D(pajak)
    vc = {"total_amount": total, "tax_amount": D(pajak), "credit_number": "VC-1"}
    b = await NDR.baris_posting_nd(None, T, vc, HUTANG, RETUR, False)
    assert [(x[0], x[1], x[2]) for x in b] == harap
    assert sum(D(str(x[1])) for x in b) == sum(D(str(x[2])) for x in b)


def test_konstanta_tolak_non_pkp_hilang_dari_aplikasi():
    for f in APP.rglob("*.py"):
        assert "PESAN_NON_PKP_PEMBELIAN" not in f.read_text(), f
