"""P2P-3b (25 Okt 2026, MASTER; kembar AS-G7b NK): POST /vendor-credits/preview = penyusun BERSAMA _susun_nd (create + pratinjau) + penentu
posting NDR.rencana_posting atas ND MAYA (baris maya untuk HPP/stok), transaksi READ ONLY, nomor = perkiraan. Perilaku nyata (pratinjau ==
create + posting tertulis, nomor perkiraan == terbit, blok == galat penulis, net 0) = harness scripts/ops/uji-nyata-p2p3b."""
import ast
import asyncio
import inspect
import textwrap
import re
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import vendor_credits as VC
from app.schemas.vendor_credits import CreateVendorCreditRequest
from app.services import nota_debit_rencana as NDR

APP = Path(VC.__file__).resolve().parents[1]
TULIS = re.compile(r"\b(UPDATE\s+\w+\s+SET|INSERT\s+INTO|DELETE\s+FROM)\b|FOR UPDATE|pg_advisory|generate_vendor_credit_number", re.I)


def _src(fn) -> str:
    return inspect.getsource(fn)


def test_rute_pratinjau_read_only_tanpa_tulis():
    s = _src(VC.preview_create_vendor_credit)
    assert "conn.transaction(readonly=True)" in s and "await tr.rollback()" in s
    assert "_susun_nd(conn, ctx, body, bl)" in s and "NDR.perkiraan_nomor_nd(conn, tid)" in s
    assert "NDR.rencana_posting(conn, ctx, vc, pratinjau=True, items=baris)" in s
    assert not TULIS.search(s) and not TULIS.search(_src(VC._susun_nd))
    panggil = {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None))
              for c in ast.walk(ast.parse(textwrap.dedent(s))) if isinstance(c, ast.Call)}
    assert not panggil & {"create_vendor_credit", "post_vendor_credit"}  # penulis tak dipanggil
    assert {"_susun_nd", "perkiraan_nomor_nd", "rencana_posting"} <= panggil


def test_create_memakai_penyusun_bersama_urutan_tetap():
    s = _src(VC.create_vendor_credit)
    i_idem = s.index('idem_buat.mulai(conn, ctx, kunci, "VC_CREATE"')
    i_susun = s.index("_susun_nd(conn, ctx, body, NDR.Blok(False))")
    i_nomor = s.index("generate_vendor_credit_number")
    assert i_idem < i_susun < i_nomor
    assert "calculate_item_totals" not in s and "tagihan_milik" not in s  # satu-satunya salinan di _susun_nd


def test_penulis_posting_tanpa_baris_maya():
    s = _src(VC.post_vendor_credit)
    assert "await NDR.rencana_posting(conn, ctx, vc)" in s
    assert "items=" not in s[s.index("NDR.rencana_posting("):s.index("NDR.rencana_posting(") + 60]


def test_perkiraan_nomor_sama_format_dengan_generator_v302():
    mig = (APP.parents[1] / "migrations" / "V302__tanggal_bisnis_nomor_dokumen.sql").read_text()
    gen = mig[mig.index("FUNCTION public.generate_vendor_credit_number"):mig.index("FUNCTION public.generate_vendor_deposit_number")]
    q = NDR.SQL_PERKIRAAN_NOMOR_ND
    for frasa in ("vendor_credit_sequences", "TO_CHAR(tanggal_bisnis(p_tenant_id), 'YYYY-MM')", "'YYMM'", "LPAD(", ", 4, '0')"):
        assert frasa in gen, frasa
    assert "vendor_credit_sequences" in q and "TO_CHAR(tanggal_bisnis($1), 'YYYY-MM')" in q and "'YYMM'" in q and "4, '0')" in q
    assert "last_number" in q and "+ 1)" in q and "'VC-'" in q and not TULIS.search(q)


class _Conn:
    def __init__(self, vendor_ada):
        self.vendor_ada = vendor_ada

    async def fetchval(self, q, *a):
        assert "FROM vendors" in q
        return 1 if self.vendor_ada else None


def _body(**k):
    d = {"vendor_id": str(uuid.uuid4()), "vendor_name": "V", "vendor_credit_date": "2026-10-25", "reason": "pricing_error",
         "items": [{"description": "x", "quantity": 3, "unit_price": 3333, "discount_percent": 5, "tax_rate": 11}], "discount_percent": 10}
    d.update(k)
    return CreateVendorCreditRequest(**d)


def test_vendor_tak_ada_tulis_400_sama_pratinjau_blok():
    with pytest.raises(HTTPException) as e:
        asyncio.run(VC._susun_nd(_Conn(False), {"tenant_id": "t"}, _body(), NDR.Blok(False)))
    assert e.value.status_code == 400 and e.value.detail == "Vendor tidak ditemukan."
    bl = NDR.Blok(True)
    asyncio.run(VC._susun_nd(_Conn(False), {"tenant_id": "t"}, _body(), bl))
    assert bl.respons() == [{"code": "VENDOR_TIDAK_DITEMUKAN", "message": "Vendor tidak ditemukan."}]


def test_hitungan_penyusun_sama_dengan_rumus_lama():
    """Rumus create (salinan teks): hitung per baris lalu diskon/pajak dokumen. Item 7 (25 Okt, izin pemilik): int() -> 0.01 HALF_UP (q2)
    di SATU tempat untuk create + pratinjau sekaligus."""
    from decimal import Decimal
    from app.services.sales_doc_calc import q2
    b = _body()
    s = asyncio.run(VC._susun_nd(_Conn(True), {"tenant_id": "t"}, b, NDR.Blok(False)))
    it = [VC.calculate_item_totals(i.model_dump()) for i in b.items]
    sub = sum(i["subtotal"] for i in it)
    disk = q2(sub * Decimal(str(b.discount_percent)) / 100)
    assert (s["subtotal"], s["discount_amount"], s["tax_amount"], s["total_amount"]) == (sub, disk, sum(i["tax_amount"] for i in it),
                                                                                          sub - disk + sum(i["tax_amount"] for i in it))


def test_rencana_hpp_memakai_baris_maya_bila_ada():
    s = _src(NDR.rencana_hpp)
    assert "items if items is not None else await conn.fetch(\"SELECT * FROM vendor_credit_items" in s
    assert "hpp, stok = await rencana_hpp(conn, tid, vc, items)" in _src(NDR.rencana_posting)


class _ConnTagihanLain(_Conn):
    async def fetchrow(self, q, *a):
        assert "FROM bills" in q
        return {"id": a[0], "invoice_number": "PB-1", "vendor_id": uuid.uuid4(), "status": "posted", "status_v2": "posted"}


def test_tagihan_vendor_lain_tulis_400_sama_pratinjau_blok():
    """Harness putaran 1 (25 Okt): pratinjau melempar 400 (HTTP) padahal kontrak pratinjau = 200 + blok -> kini lewat bl.coba."""
    b = _body(original_bill_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as e:
        asyncio.run(VC._susun_nd(_ConnTagihanLain(True), {"tenant_id": "t"}, b, NDR.Blok(False)))
    assert e.value.status_code == 400 and e.value.detail == "Tagihan PB-1 milik vendor lain."
    bl = NDR.Blok(True)
    asyncio.run(VC._susun_nd(_ConnTagihanLain(True), {"tenant_id": "t"}, b, bl))
    assert bl.respons() == [{"code": "TAGIHAN_TIDAK_SESUAI", "message": "Tagihan PB-1 milik vendor lain."}]
