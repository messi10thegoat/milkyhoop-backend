"""Pratinjau jurnal faktur dari jalur posting (12 Okt 2026, "Tinjau = angka server").

`/preview-journal` dengan `invoice_id` faktur DRAF menjalankan `terbitkan_faktur` SUNGGUHAN di dalam transaksi lalu ROLLBACK;
baris jurnal yang tampil = jurnal yang akan terbit (termasuk alokasi Surat Jalan tanpa faktur). Uji nyata (data kaos, 6 baris
pratinjau == 6 baris jurnal terbit, nol jejak): scripts/ops/uji-nyata-sj-stok.sh.
"""
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import sales_invoices as SI  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("60000000-0000-0000-0000-0000000000f1")
CTX = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-0000000000a1"}


class _Tx:
    def __init__(self, c):
        self.c = c

    async def start(self):
        self.c.tx.append("start")

    async def rollback(self):
        self.c.tx.append("rollback")

    async def commit(self):  # pragma: no cover - tak boleh dipanggil
        self.c.tx.append("COMMIT")


class _Conn:
    def __init__(self, status="draft", rows=None, alok=(0, 0), gagal=None):
        self.status, self.rows, self.alok, self.gagal = status, rows or [], alok, gagal
        self.tx, self.terbit = [], []

    def transaction(self):
        return _Tx(self)

    async def fetchrow(self, sql, *a):
        if "FROM sales_invoices" in sql:
            return {"id": INV, "invoice_number": "INV-1", "total_amount": Decimal("1110000"), "invoice_date": None,
                    "status": self.status, "tax_amount": Decimal("110000"), "shipping_amount": Decimal("0")}
        if "sj_faktur_alokasi" in sql:
            return {"qty": Decimal(self.alok[0]), "nilai": Decimal(self.alok[1])}
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        return "delivery"

    async def fetch(self, sql, *a):
        return self.rows

    async def execute(self, sql, *a):
        return "OK"


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(s):
                return c

            async def __aexit__(s, *a):
                return False
        return _A()


def _baris(src, kode, nama, d, k):
    return {"jid": 1, "source_type": src, "journal_number": "J", "account_code": kode, "account_name": nama,
            "debit": Decimal(d), "credit": Decimal(k)}


@pytest.fixture
def terbit(monkeypatch):
    panggilan = []

    async def _fake(conn, ctx, invoice_id, number, total, tgl, apply_deposits=True):
        panggilan.append({"invoice_id": invoice_id, "apply_deposits": apply_deposits})
        conn.tx.append("terbit")
        if conn.gagal:
            raise conn.gagal
        return {"warnings": ["w-posting"]}
    monkeypatch.setattr(SI, "terbitkan_faktur", _fake)
    return panggilan


@pytest.mark.asyncio
async def test_draf_memakai_jalur_posting_lalu_rollback_nol_commit(terbit):
    c = _Conn(rows=[_baris("INVOICE", "1-10300", "Piutang", "1110000", "0"),
                    _baris("INVOICE_REVENUE", "2-10750", "Dimuka", "1000000", "0"),
                    _baris("DELIVERY_INVOICED", "5-10100", "HPP", "200000", "0"),
                    _baris("DELIVERY_INVOICED", "1-10660", "Barang Terkirim", "0", "200000")], alok=(5, 200000))
    r = await SI._pratinjau_dari_posting(_Pool(c), CTX, str(INV))
    assert c.tx == ["start", "terbit", "rollback"]            # selalu ROLLBACK, tak pernah commit
    assert terbit == [{"invoice_id": INV, "apply_deposits": False}]   # deposit bukan dampak faktur
    assert r["simulasi_posting"] is True
    ev = [(l["event"], l["journal_source"]) for l in r["journal_lines"]]
    assert ev == [("JV", "INVOICE"), ("RECOG", "INVOICE_REVENUE"), ("COGS", "DELIVERY_INVOICED"), ("COGS", "DELIVERY_INVOICED")]
    assert r["total_cogs"] == 200000.0 and r["cogs_recognized_at_invoice"] is True
    assert r["alokasi_surat_jalan"] == {"qty": 5.0, "nilai": 200000.0}
    assert any("Surat Jalan tanpa faktur" in w for w in r["warnings"]) and "w-posting" in r["warnings"]
    assert r["subtotal"] == 1000000.0 and r["tax_amount"] == 110000.0 and r["total_amount"] == 1110000.0


@pytest.mark.asyncio
async def test_rollback_juga_saat_posting_gagal(terbit):
    c = _Conn(gagal=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await SI._pratinjau_dari_posting(_Pool(c), CTX, str(INV))
    assert c.tx == ["start", "terbit", "rollback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["posted", "void", "paid"])
async def test_bukan_draf_tak_disimulasikan(terbit, status):
    c = _Conn(status=status)
    assert await SI._pratinjau_dari_posting(_Pool(c), CTX, str(INV)) is None
    assert c.tx == [] and terbit == []


@pytest.mark.asyncio
async def test_rujukan_bukan_uuid_none(terbit):
    c = _Conn()
    assert await SI._pratinjau_dari_posting(_Pool(c), CTX, "bukan-uuid") is None
    assert c.tx == [] and terbit == []


def test_pemetaan_peristiwa_menutup_semua_sumber_jurnal_posting():
    # sumber jurnal yang dihasilkan posting faktur harus punya peristiwa; yang tak dikenal tampil dengan nama sumbernya
    assert SI._EVENT_PRATINJAU["INVOICE"] == "JV"
    assert SI._EVENT_PRATINJAU["INVOICE_REVENUE"] == "RECOG"
    for s in ("INVOICE_FULFILLMENT", "SALES_INVOICE_COGS", "DELIVERY_INVOICED"):
        assert SI._EVENT_PRATINJAU[s] == "COGS"


@pytest.mark.asyncio
async def test_preview_journal_dengan_invoice_id_mengembalikan_simulasi(monkeypatch):
    sentinel = {"journal_lines": [], "simulasi_posting": True}

    async def _gp():
        return "pool"

    async def _sim(pool, ctx, ref):
        assert ref == str(INV)
        return sentinel
    monkeypatch.setattr(SI, "get_pool", _gp)
    monkeypatch.setattr(SI, "_pratinjau_dari_posting", _sim)
    monkeypatch.setattr(SI, "get_user_context", lambda req: CTX)
    r = await SI.preview_journal(SimpleNamespace(), {"invoice_id": str(INV)})
    assert r is sentinel
