"""VOID-SJ-FAKTUR (15 Okt 2026; putusan MASTER + izin LANGSUNG pemilik): Surat Jalan asal FAKTUR bisa dibatalkan mandiri lewat
endpoint yang SAMA (POST /deliveries/{id}/void[/preview]); faktur TETAP utuh (pola NetSuite Item Fulfillment).

Penjaga: (1) bagi_pendapatan murni: Σ == total jurnal PERSIS, tiap bagian 0..recognized, non-stok tak ikut, mustahil -> ValueError;
(2) rencana: semua blok berkode + pesan Indonesia (alasan, faktur tak aktif, nota kredit, jurnal sudah dibalik, pendapatan/qty tak
cocok, periode), tanpa flag pengiriman_sebelum_faktur; (3) void ber-stok: pembalik HPP + pendapatan (cermin, reversal_of, reversed_by_id),
kartu dibalik HANYA journal_id SJ ini, cache baris faktur turun, status faktur dihitung ulang, SJ voided asal 'faktur', riwayat
menyebut resi, kunci INVOICE_VOID + INVOICE_FULFILL SEBELUM membaca SJ; (4) non-stok: nol jurnal, nol kartu; (5) idempoten;
(6) void FAKTUR: kartu dibalik PER SJ (journal_id) di dalam loop SJ posted -- tak ada lagi panggilan sekali per source_id;
(7) record_inventory_reversal: filter journal_id opsional ($4), None = perilaku lama."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import sj_so as S
from app.services import sj_faktur_batal as B
from app.services.inventory_helpers import record_inventory_reversal as _KARTU_ASLI   # sebelum fixture menambalnya

T = "t-uji"
HARI = date(2026, 10, 15)
INV = uuid.uuid4()
J_HPP, J_PDPT = uuid.uuid4(), uuid.uuid4()
AKUN_HPP, AKUN_INV, AKUN_DIMUKA, AKUN_JUAL = (uuid.uuid4() for _ in range(4))
SII1, SII2, IFI1, IFI2 = (uuid.uuid4() for _ in range(4))
APP = Path(S.__file__).resolve().parent.parent


def jalan(c):
    return asyncio.run(c)


def ctx():
    return {"tenant_id": T, "user_id": uuid.uuid4()}


def sj(status="posted", stok=True):
    return {"id": uuid.uuid4(), "fulfillment_number": "SJ-2609-0003", "status": status, "asal": "faktur", "sales_order_id": None,
            "invoice_id": INV, "voided_at": None, "journal_id": J_HPP if stok else None,
            "revenue_journal_id": J_PDPT if stok else None, "fulfillment_date": HARI}


def baris(id_, sii, q, cost, q_faktur, alloc, recog, fulfilled, desc="Kaos"):
    return {"id": id_, "invoice_item_id": sii, "quantity": Decimal(q), "total_cost": Decimal(cost), "description": desc,
            "q_faktur": Decimal(q_faktur), "allocated_amount": Decimal(alloc), "recognized_amount": Decimal(recog),
            "fulfilled_qty": Decimal(fulfilled)}


class Konek:
    def __init__(self, f=None, baris_sj=None, inv_status="posted", nk=None, dibalik=False, total_pdpt=Decimal("300000"),
                 resi=None):
        self.f = f or sj()
        self.baris_sj = baris_sj if baris_sj is not None else [baris(IFI1, SII1, "3", "150000", "3", "300000", "300000", "3")]
        self.inv_status, self.nk, self.dibalik, self.total_pdpt = inv_status, nk or [], dibalik, total_pdpt
        self.resi = resi or []
        self.log, self.inserts, self.updates = [], [], []

    def _s(self, sql):
        s = " ".join(sql.split())
        self.log.append(s)
        return s

    async def fetchrow(self, sql, *a):
        s = self._s(sql)
        if "FROM invoice_fulfillments f WHERE f.id" in s:
            return self.f
        if "FROM invoice_fulfillments WHERE id" in s:
            return {"sales_order_id": None, "asal": "faktur", "invoice_id": INV}
        if "FROM sales_invoices WHERE id" in s:
            return {"id": INV, "invoice_number": "INV-2609-0048", "status": self.inv_status}
        return None

    async def fetchval(self, sql, *a):
        s = self._s(sql)
        if "reversed_by_id IS NOT NULL FROM journal_entries" in s:
            return self.dibalik
        if "SELECT total_debit FROM journal_entries" in s:
            return self.total_pdpt
        if "get_next_journal_number" in s:
            return "X-2610-0001"
        if "FROM pengiriman_serah" in s:
            return "Kurir JNE"
        if "FROM tenant_features" in s:
            raise AssertionError("SJ asal faktur tak boleh memeriksa flag pengiriman_sebelum_faktur")
        return None

    async def fetch(self, sql, *a):
        s = self._s(sql)
        if "FROM invoice_fulfillment_items ifi" in s:
            return self.baris_sj
        if "FROM credit_notes" in s:
            return [{"credit_note_number": n} for n in self.nk]
        if "FROM journal_lines WHERE journal_id" in s:
            if a[0] == J_HPP:
                return [{"account_id": AKUN_HPP, "debit": Decimal("150000"), "credit": Decimal("0"), "memo": "HPP"},
                        {"account_id": AKUN_INV, "debit": Decimal("0"), "credit": Decimal("150000"), "memo": "Persediaan"}]
            return [{"account_id": AKUN_DIMUKA, "debit": Decimal("300000"), "credit": Decimal("0"), "memo": "Dimuka"},
                    {"account_id": AKUN_JUAL, "debit": Decimal("0"), "credit": Decimal("300000"), "memo": "Penjualan"}]
        if "FROM pengiriman_resi" in s:
            return [{"nomor_resi": r} for r in self.resi]
        return []

    async def execute(self, sql, *a):
        s = self._s(sql)
        if s.startswith("INSERT"):
            self.inserts.append((s, a))
        elif s.startswith("UPDATE"):
            self.updates.append((s, a))
        return "UPDATE 1"


@pytest.fixture(autouse=True)
def _pasang(monkeypatch):
    async def hari(conn, tid):
        return HARI
    monkeypatch.setattr(B, "tanggal_dokumen", hari)
    monkeypatch.setattr(S, "tanggal_dokumen", hari)

    async def terbuka(conn, tid, tgl):
        return getattr(conn, "periode_tutup", None)
    monkeypatch.setattr(S, "_periode_terbuka", terbuka)
    ev = []

    async def riwayat(conn, tenant_id, entity_type, entity_id, entity_number, event, user_id, ringkas, meta=None, source="api"):
        ev.append((event, entity_number, ringkas, meta))
    monkeypatch.setattr(B, "catat_riwayat", riwayat)
    kartu = []

    async def balik_kartu(conn, tid, **kw):
        kartu.append(kw)
    from app.services import inventory_helpers as IH
    monkeypatch.setattr(IH, "record_inventory_reversal", balik_kartu)
    status = []

    async def hitung_status(conn, invoice_id, tid):
        status.append(invoice_id)
    from app.routers import sales_invoices as SI
    monkeypatch.setattr(SI, "_update_invoice_fulfillment_status", hitung_status)
    return {"ev": ev, "kartu": kartu, "status": status}


def kodes(r):
    return [b["code"] for b in r["blocks"]]


# ---- (1) bagi_pendapatan
def test_bagi_pendapatan_satu_baris_dan_nonstok_tak_ikut():
    b = [baris(IFI1, SII1, "3", "150000", "3", "300000", "300000", "3"), baris(IFI2, SII2, "2", "0", "2", "0", "0", "2", "Jasa")]
    assert B.bagi_pendapatan(Decimal("300000"), b) == {IFI1: Decimal("300000")}
    assert B.bagi_pendapatan(Decimal("0"), b) == {IFI1: Decimal("0")}


def test_bagi_pendapatan_banyak_baris_jumlah_persis():
    b = [baris(IFI1, SII1, "1", "10", "3", "100", "100", "3"), baris(IFI2, SII2, "1", "10", "3", "100", "100", "3")]
    h = B.bagi_pendapatan(Decimal("66.67"), b)
    assert h[IFI1] == Decimal("33.33") and sum(h.values()) == Decimal("66.67")


def test_bagi_pendapatan_mustahil_valueerror():
    with pytest.raises(ValueError):   # pendapatan > recognized baris
        B.bagi_pendapatan(Decimal("500"), [baris(IFI1, SII1, "1", "10", "1", "500", "400", "1")])
    with pytest.raises(ValueError):   # jurnal pendapatan tanpa baris ber-stok
        B.bagi_pendapatan(Decimal("5"), [baris(IFI1, SII1, "1", "0", "1", "5", "5", "1")])


# ---- (2) rencana
def test_rencana_faktur_lolos_tanpa_flag_dan_bercatatan():
    r = jalan(S.rencana_void(Konek(), T, uuid.uuid4(), "salah kirim"))
    assert r["blocks"] == [] and r["ulang"] is False and r["sj"]["asal"] == "faktur"
    assert "INV-2609-0048 tetap berlaku" in r["notes"][0] and "perlu dikirim" in r["notes"][0]
    assert r["pendapatan"] == {IFI1: Decimal("300000")}


def test_rencana_semua_blok_berkode_dan_berbahasa_indonesia():
    c = Konek(inv_status="void", nk=["NK-2610-0001"], dibalik=True,
              baris_sj=[baris(IFI1, SII1, "3", "150000", "3", "300000", "100", "1")])
    c.periode_tutup = "Periode Oktober 2026 sudah ditutup."
    r = jalan(S.rencana_void(c, T, uuid.uuid4(), " "))
    assert kodes(r) == ["SJ_ALASAN_WAJIB", "SJ_FAKTUR_TIDAK_AKTIF", "SJ_FAKTUR_ADA_NOTA_KREDIT", "SJ_FAKTUR_JURNAL_SUDAH_DIBALIK",
                        "SJ_FAKTUR_PENDAPATAN_TAK_COCOK", "SJ_FAKTUR_QTY_TAK_COCOK", "SJ_PERIODE_TUTUP"]
    teks = " ".join(b["message"] for b in r["blocks"])
    assert "NK-2610-0001" in teks and "batalkan nota kreditnya dulu" in teks and "dibalik dua kali" in teks
    assert r["blocks"][0]["status"] == 422 and all(b["status"] == 409 for b in r["blocks"][1:])


def test_rencana_ulang_tanpa_blok():
    r = jalan(S.rencana_void(Konek(f=sj(status="voided")), T, uuid.uuid4(), None))
    assert r["ulang"] is True and r["blocks"] == []


# ---- (3) void ber-stok
def test_void_stok_membalik_hpp_pendapatan_kartu_cache_dan_status(_pasang):
    c = Konek(resi=["JNE123"])
    h = jalan(S.void(c, ctx(), uuid.uuid4(), "salah kirim"))
    assert h == {"ulang": False}
    je = [i for i in c.inserts if i[0].startswith("INSERT INTO journal_entries")]
    assert [(x[1][5], x[1][6], x[1][10]) for x in je] == [("INVOICE_FULFILLMENT_REVERSAL", INV, J_HPP),
                                                       ("INVOICE_REVENUE_REVERSAL", INV, J_PDPT)]
    jl = [(i[1][3], i[1][4], i[1][5]) for i in c.inserts if i[0].startswith("INSERT INTO journal_lines")]
    assert jl == [(AKUN_HPP, Decimal("0"), Decimal("150000")), (AKUN_INV, Decimal("150000"), Decimal("0")),
                  (AKUN_DIMUKA, Decimal("0"), Decimal("300000")), (AKUN_JUAL, Decimal("300000"), Decimal("0"))]
    assert [u[1][0] for u in c.updates if "SET reversed_by_id" in u[0]] == [J_HPP, J_PDPT]
    k, = _pasang["kartu"]
    assert (k["source_type"], k["source_id"], k["journal_id"]) == ("INVOICE_FULFILLMENT", INV, J_HPP)
    sii = [u for u in c.updates if "UPDATE sales_invoice_items" in u[0]]
    assert len(sii) == 1 and sii[0][1][:3] == (SII1, "3", "300000")
    assert "fulfilled_qty = fulfilled_qty - $2" in sii[0][0] and "recognized_amount = recognized_amount - $3" in sii[0][0]
    assert _pasang["status"] == [INV]
    vo = [u for u in c.updates if "SET status = 'voided'" in u[0]]
    assert len(vo) == 1 and "asal = 'faktur'" in vo[0][0]
    (ev, nomor, ringkas, meta), = _pasang["ev"]
    assert ev == "SURAT_JALAN_FAKTUR_DIBATALKAN" and "resi JNE123 ikut batal" in ringkas and meta["resi"] == ["JNE123"]
    assert not any("DELETE" in s for s in c.log)          # serah/resi/lampiran tidak dihapus


def test_void_mengunci_faktur_sebelum_membaca_sj():
    c = Konek()
    jalan(S.void(c, ctx(), uuid.uuid4(), "x"))
    i_void = next(i for i, s in enumerate(c.log) if "pg_advisory_xact_lock" in s and "hashtext($2)" not in s)
    i_fulfill = next(i for i, s in enumerate(c.log) if "pg_advisory_xact_lock(hashtext($1), hashtext($2))" in s)
    i_baca = next(i for i, s in enumerate(c.log) if "FROM invoice_fulfillments f WHERE f.id" in s)
    assert i_void < i_fulfill < i_baca and "FOR UPDATE" in c.log[i_baca]


def test_void_diblok_tidak_menulis():
    c = Konek(nk=["NK-1"])
    with pytest.raises(HTTPException) as e:
        jalan(S.void(c, ctx(), uuid.uuid4(), "x"))
    assert e.value.detail["code"] == "SJ_FAKTUR_ADA_NOTA_KREDIT" and not c.inserts and not c.updates


# ---- (4) non-stok
def test_void_nonstok_nol_jurnal_nol_kartu(_pasang):
    c = Konek(f=sj(stok=False), baris_sj=[baris(IFI1, SII1, "1", "0", "1", "0", "0", "1", "Jasa")], total_pdpt=None)
    jalan(S.void(c, ctx(), uuid.uuid4(), "batal"))
    assert not [i for i in c.inserts if "journal_" in i[0]] and not _pasang["kartu"]
    sii, = [u for u in c.updates if "UPDATE sales_invoice_items" in u[0]]
    assert sii[1][1:3] == ("1", "0")


# ---- (5) idempoten
def test_void_ulang_tanpa_tulis():
    c = Konek(f=sj(status="voided"))
    assert jalan(S.void(c, ctx(), uuid.uuid4(), "x")) == {"ulang": True}
    assert not c.inserts and not c.updates


# ---- (6) void faktur: kartu per SJ
def test_void_faktur_membalik_kartu_per_sj_journal_id():
    from app.routers import sales_invoices as SI
    src = inspect.getsource(SI._tulis_void_faktur)
    assert src.count('source_type="INVOICE_FULFILLMENT",') == 1 and 'journal_id=f["journal_id"]' in src
    assert "single call since same source_id" not in src
    i_loop, i_kartu = src.index("for f in fulfillments:"), src.index('source_type="INVOICE_FULFILLMENT",')
    i_akhir = src.index("# 0c. Mark fulfillment as voided")
    assert i_loop < i_kartu < i_akhir                     # di DALAM blok HPP tiap SJ, bukan sesudah loop
    rencana = inspect.getsource(SI._rencana_void_faktur)
    assert "status = 'posted'" in rencana                 # SJ yang sudah dibatalkan mandiri tak diproses lagi


# ---- (7) helper kartu
def test_record_inventory_reversal_filter_journal_id_opsional():
    tanya = []

    class K:
        async def fetch(self, sql, *a):
            tanya.append((sql, a))
            return []
    jalan(_KARTU_ASLI(K(), T, "INVOICE_FULFILLMENT", INV, uuid.uuid4(), reversal_date=HARI, journal_id=J_HPP))
    jalan(_KARTU_ASLI(K(), T, "INVOICE_FULFILLMENT", INV, uuid.uuid4(), reversal_date=HARI))
    assert "($4::uuid IS NULL OR journal_id = $4::uuid)" in tanya[0][0]
    assert tanya[0][1][3] == J_HPP and tanya[1][1][3] is None


# ---- (8) rute
def test_rute_void_tanpa_pastikan_aktif_dan_pratinjau_bercatatan():
    src = (APP / "routers" / "sj_so.py").read_text()
    a = src.index("async def preview_void_sj")
    isi = src[a:src.index("# Laporan", a)]
    assert "S.pastikan_aktif" not in isi
    assert '"asal": r["sj"]["asal"]' in isi and '"notes": r.get("notes", [])' in isi
    assert "await pastikan_aktif(conn, tid)" in inspect.getsource(S.rencana_void)
