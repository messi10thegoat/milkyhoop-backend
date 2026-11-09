"""JURNAL-DIBALIK (10 Nov 2026, putusan MASTER): tagihan yang jurnalnya sudah dibalik di luar void (data uji kaos 3 Sep, BILL-2609-0028..0035;
jalur /journals/reverse untuk jurnal dokumen ditutup Gate 5 833ebfb17) ditolak berkalimat oleh penentu bersama tagihan_aksi.blok_void
(penulis void_bill 400 + pratinjau + aksi_tersedia fpbatal), bukan 500 idx_je_single_reversal. Bukti salinan: uji-salinan-void-null.sh."""
import asyncio
import uuid

import pytest

from app.services import tagihan_aksi as TA

T = "kaos-biru-konveksi"
JID = uuid.uuid4()


def run(c):
    return asyncio.run(c)


class Kon:
    def __init__(self, pembalik):
        self.pembalik, self.args = pembalik, None

    async def fetchval(self, sql, *a):
        assert "reversed_by_id" in sql and "je.tenant_id = $2" in sql and "rb.tenant_id = je.tenant_id" in sql
        self.args = a
        return self.pembalik

    async def fetch(self, sql, *a):
        return []


def _bill(journal_id=JID, status="posted", status_v2="posted"):
    return {"id": uuid.uuid4(), "invoice_number": "BILL-2609-0035", "status": status, "status_v2": status_v2, "journal_id": journal_id}


def test_jurnal_sudah_dibalik_ditolak_berkalimat():
    k = Kon("JV-2609-0097")
    b = run(TA.blok_void(k, T, _bill()))
    assert [x["code"] for x in b] == ["JURNAL_SUDAH_DIBALIK"] and k.args == (JID, T)
    assert b[0]["message"] == ("Jurnal faktur pembelian BILL-2609-0035 sudah dibalik oleh JV-2609-0097, jadi faktur ini tidak bisa dibatalkan "
                               "lagi. Hubungi dukungan untuk merapikan datanya.")


def test_jurnal_utuh_lanjut_ke_aturan_lama(monkeypatch):
    async def bukan(*a):
        return False
    monkeypatch.setattr(TA, "tagihan_pembuka", bukan)
    assert run(TA.blok_void(Kon(None), T, _bill())) == []


def test_draf_tanpa_jurnal_tak_bertanya_jurnal(monkeypatch):
    async def bukan(*a):
        return False
    monkeypatch.setattr(TA, "tagihan_pembuka", bukan)

    class TanpaJurnal(Kon):
        async def fetchval(self, sql, *a):
            raise AssertionError("tagihan tanpa jurnal tak boleh memeriksa pembalikan")
    assert run(TA.blok_void(TanpaJurnal(None), T, _bill(journal_id=None, status="draft", status_v2="draft"))) == []


@pytest.mark.parametrize("bill,kode", [(_bill(status="void", status_v2="void"), "SUDAH_BATAL"),
                                       (_bill(status="draft", status_v2=None), "STATUS_SIKLUS_KOSONG")])
def test_urutan_sudah_batal_dan_null_mendahului(bill, kode):
    assert run(TA.blok_void(Kon("JV-1"), T, bill))[0]["code"] == kode
