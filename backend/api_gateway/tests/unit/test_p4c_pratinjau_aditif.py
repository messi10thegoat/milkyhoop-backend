"""U-P4c (2 Nov 2026, tiket P4 BE untuk FE): (a) pratinjau terima bayar SO/faktur membawa journals[] + bank_transactions[] dari penentu
YANG SAMA (rencana_penerimaan_ro, sumber /receive-payments/preview); (b) /bill-payments/preview membawa payment_number_preview +
payment_number_estimated dari perkiraan_nomor_bayar (penghitung SAMA, baca saja). Format nomor = SATU penentu format_nomor_bayar,
dipakai juga penulis generate_payment_number -> nomor terbit IDENTIK dengan rumus lama (syarat MASTER)."""
import ast
import asyncio
import inspect
from datetime import date

from app.routers import bill_payments as BP
from app.services import so_pelunasan as SP


def _lama(year_month, n):
    # rumus PERSIS generate_payment_number sebelum U-P4c (deploy/master e68d273b) -- pembanding dasar
    return f"PAY-{year_month.replace('-', '')}-{n:04d}"


KASUS = [("2026-11", 1), ("2026-11", 9), ("2026-11", 42), ("2026-11", 9999), ("2026-11", 10000), ("2026-11", 123456),
         ("2026-12", 1), ("2027-01", 1), ("2027-01", 10001)]


def test_format_nomor_identik_rumus_lama():
    for ym, n in KASUS:
        assert BP.format_nomor_bayar(ym, n) == _lama(ym, n), (ym, n)
    assert BP.format_nomor_bayar("2026-11", 7) == "PAY-202611-0007"
    assert BP.format_nomor_bayar("2026-11", 10000) == "PAY-202611-10000"


class _Tulis:
    def __init__(self, n):
        self.n, self.log = n, []

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        return {"last_number": self.n}


def test_penulis_terbit_identik_lintas_bulan(monkeypatch):
    for ym, n in KASUS:
        y, m = (int(x) for x in ym.split("-"))

        async def tgl(conn, tid, _d=date(y, m, 15)):
            return _d
        monkeypatch.setattr(BP, "tanggal_dokumen", tgl)
        k = _Tulis(n)
        assert asyncio.run(BP.generate_payment_number(k, "t")) == _lama(ym, n)
        assert "INSERT INTO bill_payment_sequences" in k.log[0][0] and k.log[0][1] == ("t", ym)


class _Baca:
    def __init__(self, last):
        self.last, self.log = last, []

    async def fetchval(self, sql, *a):
        self.log.append(sql)
        return self.last

    async def fetchrow(self, sql, *a):
        raise AssertionError("perkiraan tak boleh menaikkan penghitung")

    async def execute(self, sql, *a):
        raise AssertionError("perkiraan tak boleh menulis")


def test_perkiraan_baca_saja_sama_dengan_terbit_berikutnya(monkeypatch):
    async def tgl(conn, tid):
        return date(2026, 11, 2)
    monkeypatch.setattr(BP, "tanggal_dokumen", tgl)
    k = _Baca(41)
    assert asyncio.run(BP.perkiraan_nomor_bayar(k, "t")) == "PAY-202611-0042" == _lama("2026-11", 42)
    assert all(q.lstrip().upper().startswith("SELECT") and "FOR UPDATE" not in q.upper() for q in k.log)
    assert asyncio.run(BP.perkiraan_nomor_bayar(_Baca(None), "t")) == "PAY-202611-0001"
    # bulan & tabel SAMA dengan penulis
    assert "bill_payment_sequences" in k.log[0] and "year_month = $2" in k.log[0]


def _panggil(fn, nama):
    pohon = ast.parse(inspect.getsource(fn).lstrip())
    return [n for n in ast.walk(pohon) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(nama)]


def test_penulis_dan_pratinjau_satu_penentu_format():
    assert _panggil(BP.generate_payment_number, "format_nomor_bayar")
    assert "{row['last_number']:04d}" not in inspect.getsource(BP.generate_payment_number)
    assert _panggil(BP.perkiraan_nomor_bayar, "format_nomor_bayar")
    s = inspect.getsource(BP.preview_create_bill_payment)
    assert _panggil(BP.preview_create_bill_payment, "perkiraan_nomor_bayar")
    assert s.index("transaction(readonly=True)") < s.index("perkiraan_nomor_bayar(") < s.index("tr.rollback()")
    assert '"payment_number_preview": nomor, "payment_number_estimated": True' in s


def test_jurnal_bank_dari_penentu_yang_sama():
    assert SP._jurnal_bank(None) == {"journals": [], "bank_transactions": []}
    j, b = [{"journal_number": None, "lines": []}], [{"bank_account": "BCA", "amount": 1.0}]
    d = SP._jurnal_bank({"journals": j, "bank_transactions": b})
    assert d["journals"] is j and d["bank_transactions"] is b
    s = inspect.getsource(SP._jalankan_inti)
    assert '"journals": rr["journals"], "bank_transactions": rr["bank_transactions"]' in s
    for fn in (SP.pratinjau_pelunasan_so, SP.pratinjau_pelunasan_faktur):
        assert "**_jurnal_bank(hasil)," in inspect.getsource(fn), fn.__name__
