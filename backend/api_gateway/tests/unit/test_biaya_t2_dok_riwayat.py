"""Biaya t2 langkah 3/4: bundel dokumen + riwayat satu biaya (services/biaya_riwayat). Conn palsu: kueri dikenali dari kata kuncinya."""
import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest

from app.services import biaya_riwayat as BR
from app.schemas.expenses import ExpenseDocumentsResponse, ExpenseHistoryResponse

EID, JID, RID, AID, U1, U2 = (uuid.uuid4() for _ in range(6))
T0 = datetime(2026, 11, 7, 1, tzinfo=timezone.utc)


def _exp(status="posted", journal=True):
    return {"id": EID, "expense_number": "EXP-0001", "expense_date": date(2026, 11, 7), "status": status,
            "total_amount": 150000, "account_name": "Beban Listrik", "paid_through_name": "Kas Toko",
            "journal_id": JID if journal else None, "created_at": T0, "created_by": U1}


class Conn:
    def __init__(self, exp, rev=False, bukti=0, void_by=None):
        self.exp, self.rev, self.bukti, self.void_by = exp, rev, bukti, void_by
        self.kueri = []

    async def fetchrow(self, q, *a):
        self.kueri.append((q, a))
        if "FROM expenses" in q:
            return self.exp
        if "reversal_of_id" in q:
            return ({"id": RID, "journal_number": "REV-EXP-0001", "journal_date": date(2026, 11, 8), "total_debit": 150000,
                     "description": "Reversal: EXP-0001 - salah input", "created_at": T0.replace(day=8)} if self.rev else None)
        if "FROM journal_entries" in q:
            return {"id": JID, "journal_number": "JE-1", "journal_date": date(2026, 11, 7), "total_debit": 150000,
                    "created_at": T0.replace(hour=2), "created_by": U2}
        raise AssertionError(q)

    async def fetch(self, q, *a):
        self.kueri.append((q, a))
        assert "FROM expense_attachments" in q
        return [{"id": AID, "file_name": "struk.jpg", "uploaded_at": T0.replace(hour=3), "uploaded_by": U1}] * self.bukti

    async def fetchval(self, q, *a):
        self.kueri.append((q, a))
        assert "FROM bank_transactions" in q
        return self.void_by


def jalan(c):
    return asyncio.run(c)


def test_tak_ada_none():
    assert jalan(BR.dokumen_biaya(Conn(None), "t", EID)) is None
    assert jalan(BR.riwayat_biaya(Conn(None), "t", EID)) is None


def test_bundel_posted_tanpa_bukti():
    d = jalan(BR.dokumen_biaya(Conn(_exp()), "t", EID))
    assert d["anchor"]["position_text"] == "Dibayar dari Kas Toko · tanpa bukti"   # SAMA dgn biaya_daftar.position_text
    assert d["anchor"]["total"] == "150000.00" and d["anchor"]["account_name"] == "Beban Listrik"
    assert [g["key"] for g in d["groups"]] == ["biaya", "jurnal"]
    assert d["default"] == {"group": "biaya", "index": 0}
    # model respons MENDEKLARASIKAN medannya (pola BUG-097: medan tak dideklarasi dibuang diam)
    m = ExpenseDocumentsResponse(success=True, data=d).model_dump()
    assert m["data"]["anchor"]["position_text"] == d["anchor"]["position_text"]


def test_bundel_void_ada_pembalik_dan_bukti():
    d = jalan(BR.dokumen_biaya(Conn(_exp("void"), rev=True, bukti=2), "t", EID))
    assert d["anchor"]["position_text"] == "Dibatalkan"
    g = {x["key"]: x["docs"] for x in d["groups"]}
    assert [x["number"] for x in g["jurnal"]] == ["JE-1", "REV-EXP-0001"]
    assert g["jurnal"][0]["batal"] is True and g["biaya"][0]["batal"] is True
    assert len(g["bukti"]) == 2 and g["bukti"][0]["kind"] == "attachment"


def test_bundel_draf_tanpa_jurnal():
    d = jalan(BR.dokumen_biaya(Conn(_exp("draft", journal=False)), "t", EID))
    assert d["anchor"]["position_text"] == "Draf" and [g["key"] for g in d["groups"]] == ["biaya"]


def test_filter_tenant_eksplisit_semua_kueri():
    c = Conn(_exp("void"), rev=True, bukti=1, void_by=U2)
    jalan(BR.dokumen_biaya(c, "tenantX", EID))
    for q, a in c.kueri:
        assert "tenant_id" in q and "tenantX" in a, q


@pytest.fixture
def tangkap(monkeypatch):
    hasil = {}

    async def palsu(conn, tid, k, ent, lihat, limit):
        hasil["ev"], hasil["ent"] = k.ev, ent
        return [], len(k.ev)
    monkeypatch.setattr(BR, "_selesaikan", palsu)
    return hasil


def test_riwayat_lengkap(tangkap):
    out = jalan(BR.riwayat_biaya(Conn(_exp("void"), rev=True, bukti=1, void_by=U2), "t", EID))
    jenis = [e["jenis"] for e in tangkap["ev"]]
    assert jenis == ["BIAYA_DIBUAT", "BIAYA_DIPOSTING", "BIAYA_DIBATALKAN", "BUKTI_DITAMBAH"]
    ev = {e["jenis"]: e for e in tangkap["ev"]}
    assert ev["BIAYA_DIBATALKAN"]["ringkas"] == "Biaya EXP-0001 dibatalkan (void): salah input"
    assert ev["BIAYA_DIBATALKAN"]["aktor_id"] == str(U2)
    assert ev["BIAYA_DIPOSTING"]["aktor_id"] == str(U2) and "Kas Toko" in ev["BIAYA_DIPOSTING"]["ringkas"]
    assert "Rp" not in ev["BIAYA_DIBUAT"]["ringkas"]  # nominal saat dibuat tak tersimpan (Law 12): tak diklaim
    assert tangkap["ent"] == {}  # biaya tak menulis audit_logs
    ExpenseHistoryResponse(success=True, data=out)


def test_riwayat_batal_tanpa_bank_aktor_null(tangkap):
    jalan(BR.riwayat_biaya(Conn(_exp("void"), rev=True, void_by=None), "t", EID))
    ev = {e["jenis"]: e for e in tangkap["ev"]}
    assert ev["BIAYA_DIBATALKAN"]["aktor_id"] is None


def test_alasan_dari_deskripsi():
    assert BR.alasan_dari_deskripsi("Reversal: EXP-1 - dobel", "EXP-1") == "dobel"
    assert BR.alasan_dari_deskripsi("Reversal: EXP-1 - ", "EXP-1") is None
    assert BR.alasan_dari_deskripsi("lain", "EXP-1") is None
    assert BR.alasan_dari_deskripsi(None, "EXP-1") is None
