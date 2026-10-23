"""Nota Debit U3d (23 Okt 2026): penentu per aksi SATU (services/nota_debit_rencana) untuk tulis + pratinjau; pratinjau READ ONLY tanpa
kunci/sequence (BUKAN tulis-lalu-rollback NK); penulis memanggil penentu yang sama (perilaku tak berubah)."""
import asyncio
import re
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import nota_debit_rencana as NDR

APP = Path(__file__).resolve().parents[2] / "app"
R = (APP / "routers/vendor_credits.py").read_text()
NDS = (APP / "services/nota_debit.py").read_text()
SRC = (APP / "services/nota_debit_rencana.py").read_text()


def _fungsi(src, mulai, akhir):
    return src[src.index(mulai):src.index(akhir)]


def test_blok_tulis_melempar_sama_pratinjau_mengumpulkan():
    t = NDR.Blok(False)
    with pytest.raises(HTTPException) as e:
        t.galat("X", 409, {"code": "ND_SUDAH_BERTAUT", "message": "m"})
    assert e.value.status_code == 409 and e.value.detail == {"code": "ND_SUDAH_BERTAUT", "message": "m"}
    p = NDR.Blok(True)
    p.galat("X", 409, {"code": "ND_SUDAH_BERTAUT", "message": "m"})
    p.galat("Y", 400, "teks")
    assert p.respons() == [{"code": "ND_SUDAH_BERTAUT", "message": "m"}, {"code": "Y", "message": "teks"}]


class _K:
    def __init__(self, pkp):
        self.pkp = pkp


def _vc(pajak=0):
    return {"id": uuid4(), "credit_number": "VC-1", "total_amount": 1100, "tax_amount": pajak, "vendor_name": "V"}


def test_baris_posting_tanpa_dan_dengan_pajak(monkeypatch):
    ap, kr, ppn = uuid4(), uuid4(), uuid4()

    async def pkp(conn, tid, role):
        return ppn if conn.pkp else None
    monkeypatch.setattr(NDR, "resolve_account_id_by_role_if_pkp", pkp)
    b = asyncio.run(NDR.baris_posting_nd(_K(True), "t", _vc(), ap, kr, False))
    assert b == [(ap, 1100, 0, "Pengurangan Hutang - VC-1", "hutang"), (kr, 0, 1100, "Retur Pembelian - VC-1", "kredit")]
    b = asyncio.run(NDR.baris_posting_nd(_K(True), "t", _vc(100), ap, kr, True))
    assert b[1] == (kr, 0, 1000, "Diskon Pembelian - VC-1", "kredit") and b[2] == (ppn, 0, 100, "PPN Retur - VC-1", "pajak")
    with pytest.raises(HTTPException) as e:
        asyncio.run(NDR.baris_posting_nd(_K(False), "t", _vc(100), ap, kr, False))
    assert e.value.status_code == 422


def test_baris_refund():
    k, ap = uuid4(), uuid4()
    assert NDR.baris_refund_nd(k, ap, 500, "VC-1") == [(k, 500, 0, "Terima Refund - VC-1"), (ap, 0, 500, "Hutang Refund - VC-1")]


def test_penentu_tak_menulis_tak_menyentuh_sequence():
    kode = "\n".join(l for l in SRC.splitlines() if not l.lstrip().startswith("#"))
    kode = re.sub(r'"""[\s\S]*?"""', "", kode)
    assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", kode, re.I)
    assert "get_next_journal_number" not in kode and "pg_advisory" not in kode and "record_inventory_outbound(" not in kode
    # kunci baris HANYA di jalur tulis
    assert kode.count('("" if pratinjau else " FOR UPDATE")') >= 3


def test_penulis_memakai_penentu():
    post = _fungsi(R, "async def post_vendor_credit(", "# APPLY VENDOR CREDIT TO BILL(S)")
    assert "await NDR.rencana_posting(conn, ctx, vc)" in post and "await NDR.baris_posting_nd(" in post
    assert "Cannot post vendor credit with status" not in post and post.count("INSERT INTO journal_lines") == 1
    refund = _fungsi(R, "async def receive_refund(", "# VOID VENDOR CREDIT")
    assert "await NDR.rencana_kembali(conn, ctx, vendor_credit_id, body)" in refund and "NDR.baris_refund_nd(" in refund
    assert "Cannot receive refund" not in refund and refund.count("INSERT INTO journal_lines") == 1
    void = _fungsi(R, "async def void_vendor_credit(", "# PRATINJAU AKSI")
    assert "await NDR.rencana_batal(conn, ctx, vendor_credit_id, body.reason)" in void and "already voided" not in void
    assert "rencana_terapkan(conn, tid, vc_id, bill_id, jumlah)" in NDS and "rencana_lepas(conn, tid, vc_id, alasan)" in NDS
    assert "Hanya nota debit yang sudah dibukukan" not in NDS  # pesan hanya di penentu


def test_pratinjau_read_only_dan_rute():
    from app.routers import vendor_credits as VR
    pv = _fungsi(R, "async def _pratinjau_nd(", "@router.post(\"/{vendor_credit_id}/post/preview\")")
    assert "transaction(readonly=True)" in pv and "tr.rollback()" in pv and "pg_advisory" not in pv
    paths = {(r.path, tuple(sorted(r.methods))) for r in VR.router.routes}
    for a in ("post", "apply", "unapply", "refund", "receive-refund", "void"):
        assert (f"/{{vendor_credit_id}}/{a}/preview", ("POST",)) in paths


def test_izin_pratinjau_sebelum_pola_umum():
    m = (APP / "middleware/permission_middleware.py").read_text()
    i_pv = m.index('(r"^/api/vendor-credits/[^/]+/(post|refund|receive-refund)/preview$", ["POST"], "debit_note", "P")')
    assert i_pv < m.index('(r"^/api/vendor-credits", ["POST"], "debit_note", "C")')
    assert '(r"^/api/vendor-credits/[^/]+/(void|unapply)/preview$", ["POST"], "debit_note", "V")' in m
    assert '(r"^/api/vendor-credits/[^/]+/apply/preview$", ["POST"], "debit_note", "A")' in m
