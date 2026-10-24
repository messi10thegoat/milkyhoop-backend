"""AS-G7b (24 Okt 2026, desain disetujui MASTER): pratinjau BUAT nota kredit = rencana + penentu posting (rencana_posting_nk) atas NK
MAYA, transaksi READ ONLY -- bukan buat + posting nyata di savepoint. Perilaku nyata (LAMA == BARU, perkiraan nomor == nomor terbit,
pratinjau tak menunggu kunci penulis, net 0) = harness scripts/ops/uji-nyata-as-g7b."""
import ast
import inspect
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers import credit_notes as CR
from app.services import cn_tertunda as CT
from app.services import nota_kredit_rencana as NKR

APP = Path(CR.__file__).resolve().parents[1]
TULIS = re.compile(r"\b(UPDATE\s+\w+\s+SET|INSERT\s+INTO|DELETE\s+FROM)\b|FOR UPDATE|pg_advisory", re.I)


def _panggilan(fn) -> set:
    src = inspect.getsource(fn)
    return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None))
            for c in ast.walk(ast.parse(src.lstrip() if not src.startswith(" ") else "if 1:\n" + src)) if isinstance(c, ast.Call)}


def test_pratinjau_tanpa_penulis_dan_rute_read_only():
    p = _panggilan(CR.pratinjau_nota_kredit)
    assert "buat_nota_kredit" not in p and "posting_nota_kredit" not in p
    assert {"rencana_posting_nk", "perkiraan_nomor_nk", "periksa_tautan_baris_faktur", "catatan_kelebihan_nk"} <= p
    assert "conn.transaction(readonly=True)" in inspect.getsource(CR.preview_credit_note)


def test_rencana_buat_cek_bisa_dinotakan_tanpa_kunci():
    src = inspect.getsource(CR._rencana_nota_kredit)
    assert "periksa_bisa_dinotakan(conn, tid, faktur[\"id\"], [i.model_dump() for i in body.items], kunci=False)" in src


def test_penulis_posting_memanggil_penentu_tanpa_maya():
    """Penulis tak berubah: penentu dipanggil TANPA maya (baca NK & baris dari DB seperti dulu)."""
    src = inspect.getsource(CR.posting_nota_kredit)
    for m in re.finditer(r"rencana_posting_nk\(([^)]*)\)", src):
        assert "maya" not in m.group(1)


def test_perkiraan_nomor_sama_format_dengan_generator_v302():
    mig = (APP.parents[1] / "migrations" / "V302__tanggal_bisnis_nomor_dokumen.sql").read_text()
    gen = mig[mig.index("FUNCTION public.generate_credit_note_number"):mig.index("FUNCTION public.generate_customer_deposit_number")]
    q = NKR.SQL_PERKIRAAN_NOMOR_NK
    # penghitung & kunci periode yang SAMA, format yang SAMA (prefix 'CN', YYMM, LPAD 4), nilai = last_number + 1, tanpa menulis
    for frasa in ("credit_note_sequences", "TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM')", "'YYMM'", "LPAD(", ", 4, '0')"):
        assert frasa in gen, frasa
    assert "credit_note_sequences" in q and "TO_CHAR(tanggal_bisnis($1), 'YYYY-MM')" in q and "'YYMM'" in q and "4, '0')" in q
    assert "last_number" in q and "+ 1)" in q and "'CN-'" in q and not TULIS.search(q)


def test_catatan_kelebihan_tanpa_nomor_dan_tanpa_kurung():
    m = NKR.catatan_kelebihan_nk(Decimal("1500"))["message"]
    assert "(" not in m and ")" not in m and "KRD" not in m and "saldo kredit pelanggan" in m
    assert NKR.catatan_kelebihan_nk(Decimal("1"))["code"] == "CN_EXCESS_TO_CUSTOMER_CREDIT"


class _Baca:
    def __init__(self, jawab):
        self.jawab, self.sql = jawab, []

    def _cek(self, q):
        self.sql.append(q)
        assert not TULIS.search(q), q[:80]

    async def fetch(self, q, *a):
        self._cek(q)
        return self.jawab(q, a)

    async def fetchrow(self, q, *a):
        self._cek(q)
        return self.jawab(q, a)

    async def fetchval(self, q, *a):
        self._cek(q)
        return self.jawab(q, a)

    async def execute(self, q, *a):
        self._cek(q)


@pytest.mark.asyncio
async def test_hitung_untuk_nk_item_maya_tanpa_baca_credit_note_items(monkeypatch):
    def jawab(q, a):
        assert "credit_note_items" not in q, "baris NK maya tak boleh dibaca dari DB"
        return [{"id": 1, "item_id": None, "allocated_amount": 0, "recognized_amount": 0}]
    monkeypatch.setattr(CT, "porsi_tertunda", lambda sub, nk, baris, alasan: {"nk": nk})
    porsi, _ = await CT.hitung_untuk_nk(_Baca(jawab), "t", {"original_invoice_id": uuid.uuid4(), "reason": "other", "id": uuid.uuid4()},
                                        Decimal("10"), kunci=False, item_nk=[{"item_id": None, "subtotal": Decimal("10")}])
    assert porsi == {"nk": [{"item_id": None, "subtotal": Decimal("10")}]}


@pytest.mark.asyncio
async def test_restock_semua_ikutkan_baris_biaya_nol(monkeypatch):
    pid = uuid.uuid4()

    def jawab(q, a):
        if "FROM products" in q and "track_inventory" in q:
            return {"id": pid, "item_code": "B", "nama_produk": "Kaos", "track_inventory": True}
        if "get_weighted_average_cost" in q:
            return 0
        if "FROM warehouses" in q:
            return uuid.uuid4()
        raise AssertionError(q)
    monkeypatch.setattr(CR, "_cn_memulihkan_stok", lambda r: True)
    cn = {"id": uuid.uuid4(), "original_invoice_id": None, "reason": "return", "original_invoice_number": None}
    items = [{"item_id": pid, "quantity": Decimal("2")}]
    bl = NKR.Blok(True)
    assert await NKR._rencana_restock_nk(_Baca(jawab), {"tenant_id": "t"}, cn, bl, items=items) == []
    r = await NKR._rencana_restock_nk(_Baca(jawab), {"tenant_id": "t"}, cn, bl, items=items, semua=True)
    assert [(x["nama"], x["qty"], x["line_cost"]) for x in r] == [("Kaos", Decimal("2"), Decimal("0"))]
