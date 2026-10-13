"""Ubah penawaran TERKIRIM (13 Okt 2026, MASTER; pola estimate Xero/Zoho/NetSuite).

Temuan pemilik grapgrap: QUO-2610-0018 (Terkirim) tak bisa diubah sama sekali, termasuk tanggal -- PATCH memblok
semua status != draft. Kini: draf/terkirim/dilihat/kedaluwarsa (belum dijawab) BOLEH diubah, status TETAP, riwayat
"diubah sesudah dikirim" + diff medan/baris/total. Terjawab/jadi dokumen/batal TERKUNCI (Duplikat). Pratinjau
POST /quotes/{id}/edit/preview = jalur inti YANG SAMA lalu ROLLBACK.

Handler dipanggil LANGSUNG di atas DB palsu #34/#39 (SQL dijawab sesuai semantik, SQL asing = AssertionError)."""
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q
from app.schemas.quotes import UpdateQuoteRequest

from .test_audit_penawaran_diubah import DBT, _audit
from .test_t39_tarif_penawaran import QUOTE_ID, _req, pasang  # noqa: F401

HARI_INI = date(2026, 10, 13)
KEPALA = {"quote_date": date(2026, 10, 13), "expiry_date": date(2026, 10, 27), "notes": "lama",
          "subtotal": Decimal("100000.00"), "discount_amount": Decimal("0"), "tax_amount": Decimal("0"),
          "total_amount": Decimal("100000.00"), "customer_name": "Pelanggan"}


class DBU(DBT):
    """+ status penawaran yang bisa diatur, potret sebelum/sesudah, rujukan SO/faktur, rekam rollback transaksi."""

    def __init__(self, status="sent", kepala=None, kepala_sesudah=None, baris=None, baris_sesudah=None, rujukan=None):
        super().__init__()
        self.status = status
        self.potret_kepala = [dict(kepala or KEPALA), dict(kepala_sesudah or kepala or KEPALA)]
        self.potret_baris = [list(baris or []), list(baris_sesudah if baris_sesudah is not None else (baris or []))]
        self.n_kepala = self.n_baris = 0
        self.rujukan = rujukan
        self.keluar = []  # tipe galat di __aexit__ tiap transaksi (None = commit)

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT id, status FROM quotes"):
            return {"id": a[0], "status": self.status}
        if s.startswith("SELECT * FROM quotes WHERE id"):
            k = self.potret_kepala[min(self.n_kepala, 1)]
            self.n_kepala += 1
            return {"id": a[0], "status": self.status, **k}
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM quote_items WHERE quote_id"):
            b = self.potret_baris[min(self.n_baris, 1)]
            self.n_baris += 1
            return b
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT 1 FROM sales_orders WHERE tenant_id = $1 AND quote_id = $2"):
            return self.rujukan
        return await super().fetchval(sql, *a)

    def transaction(self):
        db = self

        class T:
            async def __aenter__(self):
                db.dalam += 1
                return db

            async def __aexit__(self, tipe, *e):
                db.dalam -= 1
                db.keluar.append(tipe)
                return False
        return T()

    def tulis_quotes(self):
        return [a for (s, _, a) in self.urut if s.startswith("UPDATE quotes")]


@pytest.fixture(autouse=True)
def hari_ini(monkeypatch):
    async def _t(conn, tid):
        return HARI_INI
    monkeypatch.setattr(Q, "tanggal_dokumen", _t)


# ---- status: yang boleh dan yang terkunci ----

@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["sent", "viewed", "expired"])
async def test_belum_dijawab_boleh_diubah_status_tetap(pasang, status):
    db = pasang(DBU(status, kepala_sesudah={**KEPALA, "notes": "TES E2E baru"}))
    r = await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="TES E2E baru"))
    assert r.success and r.data["status"] == status and r.data["after_sent"] is True
    pulih = [a for (s, _, a) in db.urut if s.startswith("UPDATE quotes SET status")]
    # trg_quote_expiry membalik HANYA OLD.status='sent' -> pemulihan bersyarat hanya untuk sent
    assert len(pulih) == (1 if status == "sent" else 0)
    [(dalam, a)] = _audit(db)
    meta = json.loads(a[7])
    assert dalam and a[1] == "QUOTE_UPDATED"
    assert meta["ringkas"] == "Penawaran diubah sesudah dikirim (notes)"
    assert meta["after_sent"] is True and meta["status"] == status
    assert meta["diff"] == {"notes": ["lama", "TES E2E baru"]}
    assert db.keluar == [None], "PATCH = commit"


@pytest.mark.asyncio
async def test_tanggal_penawaran_terkirim_bisa_diubah(pasang):
    """Inti temuan pemilik: tanggal QUO-2610-0018 tak bisa diubah."""
    baru = {**KEPALA, "quote_date": date(2026, 10, 12)}
    db = pasang(DBU("sent", kepala_sesudah=baru))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(quote_date=date(2026, 10, 12)))
    assert db.tulis_quotes(), "UPDATE quotes wajib terjadi"
    [(_, a)] = _audit(db)
    assert json.loads(a[7])["diff"] == {"quote_date": ["2026-10-13", "2026-10-12"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "declined", "converted", "void"])
async def test_terjawab_atau_jadi_dokumen_terkunci_tanpa_tulis(pasang, status):
    db = pasang(DBU(status))
    with pytest.raises(HTTPException) as e:
        await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="x"))
    assert e.value.status_code == 400 and "Duplikat" in e.value.detail
    assert db.tulis_quotes() == [] and _audit(db) == []


@pytest.mark.asyncio
async def test_draf_tetap_judul_lama(pasang):
    db = pasang(DBU("draft", kepala_sesudah={**KEPALA, "notes": "b"}))
    r = await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="b"))
    [(_, a)] = _audit(db)
    meta = json.loads(a[7])
    assert meta["ringkas"] == "Penawaran diubah (notes)" and meta["after_sent"] is False
    assert r.data["after_sent"] is False


@pytest.mark.asyncio
async def test_terkirim_yang_sudah_dirujuk_pesanan_409(pasang):
    db = pasang(DBU("sent", rujukan=1))
    with pytest.raises(HTTPException) as e:
        await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="x"))
    assert e.value.status_code == 409
    assert db.tulis_quotes() == [] and _audit(db) == []


@pytest.mark.asyncio
async def test_nomor_terbit_tetap_tak_bisa_diubah(pasang):
    db = pasang(DBU("sent"))
    with pytest.raises(HTTPException) as e:
        await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(quote_number="QUO-LAIN-1"))
    assert e.value.status_code == 400 and db.tulis_quotes() == []


# ---- tanggal pada penawaran yang sudah keluar ----

@pytest.mark.asyncio
@pytest.mark.parametrize("badan,pesan", [
    ({"quote_date": date(2026, 10, 28)}, "sebelum tanggal penawaran"),          # berlaku 27 < tanggal baru 28
    ({"quote_date": date(2026, 10, 1), "expiry_date": date(2026, 10, 12)}, "masa lalu"),  # berlaku BARU lewat
    ({"expiry_date": None, "notes": "x"}, "wajib diisi"),
])
async def test_tanggal_tak_masuk_akal_422_tanpa_tulis(pasang, badan, pesan):
    db = pasang(DBU("sent"))
    with pytest.raises(HTTPException) as e:
        await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(**badan))
    assert e.value.status_code == 422 and pesan in e.value.detail
    assert db.tulis_quotes() == []


@pytest.mark.asyncio
async def test_kedaluwarsa_ubah_catatan_dengan_berlaku_lama_tak_berubah_lolos(pasang):
    """Form lama mengirim badan penuh: tanggal berlaku LAMA (sudah lewat) yang tak berubah tak boleh menolak suntingan."""
    lama = {**KEPALA, "quote_date": date(2026, 9, 1), "expiry_date": date(2026, 9, 15)}
    db = pasang(DBU("sent", kepala=lama, kepala_sesudah={**lama, "notes": "y"}))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(expiry_date=date(2026, 9, 15), notes="y"))
    assert db.tulis_quotes()


@pytest.mark.asyncio
async def test_perpanjang_saja_tetap_jalur_perpanjang(pasang, monkeypatch):
    dipanggil = []

    async def _p(conn, ctx, qid, body, quote):
        dipanggil.append(quote["status"])
        return Q.QuoteResponse(success=True, message="Quote validity extended", data={})
    monkeypatch.setattr(Q, "_perpanjang_penawaran", _p)
    pasang(DBU("viewed"))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(expiry_date=date(2026, 11, 30)))
    assert dipanggil == ["viewed"]


# ---- pratinjau: jalur sama, ROLLBACK ----

@pytest.mark.asyncio
async def test_pratinjau_rollback_dan_membawa_diff(pasang):
    db = pasang(DBU("sent", kepala_sesudah={**KEPALA, "notes": "baru", "total_amount": Decimal("90000.00")}))
    r = await Q.preview_edit_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="baru"))
    assert r.data["preview"] is True and r.data["after_sent"] is True
    assert r.data["diff"]["notes"] == ["lama", "baru"]
    assert r.data["total"] == [100000.0, 90000.0]
    assert db.keluar and db.keluar[-1] is Q._BatalkanUbahPenawaran, "transaksi pratinjau WAJIB keluar lewat galat = ROLLBACK"


@pytest.mark.asyncio
async def test_pratinjau_terkunci_galat_sama_dengan_patch(pasang):
    pasang(DBU("converted"))
    with pytest.raises(HTTPException) as e:
        await Q.preview_edit_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="x"))
    assert e.value.status_code == 400 and "Duplikat" in e.value.detail


def test_rute_pratinjau_terdaftar_post():
    rute = {(r.path, m) for r in Q.router.routes for m in getattr(r, "methods", ())}
    assert ("/{quote_id}/edit/preview", "POST") in rute


def test_izin_pratinjau_sama_dengan_patch():
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur("/api/quotes/abc/edit/preview", "POST") == ("quote", "U")
    assert izin_untuk_jalur("/api/quotes/abc", "PATCH") == ("quote", "U")
    assert izin_untuk_jalur("/api/quotes/abc/send", "POST") == ("quote", "C")  # kontrol: pola umum POST tetap C


# ---- diff murni ----

def test_beda_medan_sama_nilai_tak_masuk_dan_angka_dibanding_nilai():
    k0 = {**KEPALA, "discount_value": Decimal("5.00")}
    k1 = {**KEPALA, "discount_value": 5.0, "notes": "lama"}
    b = Q.beda_penawaran({"kepala": k0, "baris": []}, {"kepala": k1, "baris": []}, ["discount_value", "notes"])
    assert b["diff"] == {} and b["lines"] == {"added": [], "removed": [], "changed": []}


def test_beda_baris_tambah_hapus_ubah():
    b0 = [{"description": "Kaos", "quantity": Decimal("2"), "unit_price": Decimal("50000")},
          {"description": "Topi", "quantity": Decimal("1"), "unit_price": Decimal("20000")}]
    b1 = [{"description": "Kaos", "quantity": Decimal("3"), "unit_price": Decimal("50000")}]
    b = Q.beda_penawaran({"kepala": KEPALA, "baris": b0}, {"kepala": KEPALA, "baris": b1}, ["items"])
    assert b["lines"]["removed"] == ["Topi"] and b["lines"]["added"] == []
    assert b["lines"]["changed"] == [{"line": 1, "description": "Kaos", "changes": {"quantity": [2.0, 3.0]}}]
    b = Q.beda_penawaran({"kepala": KEPALA, "baris": b1}, {"kepala": KEPALA, "baris": b0}, ["items"])
    assert b["lines"]["added"] == ["Topi"]


def test_beda_total_selalu_ikut_bila_berubah():
    b = Q.beda_penawaran({"kepala": KEPALA, "baris": []},
                         {"kepala": {**KEPALA, "total_amount": Decimal("1")}, "baris": []}, [])
    assert b["diff"] == {"total_amount": [100000.0, 1.0]} and b["total"] == [100000.0, 1.0]


def test_status_boleh_ubah_persis():
    assert set(Q.STATUS_BOLEH_UBAH) == {"draft", "sent", "viewed", "expired"}


@pytest.mark.asyncio
async def test_pemulihan_status_sesudah_tulis_dan_bersyarat_expired(pasang):
    """trg_quote_expiry: sent + berlaku < tanggal bisnis -> 'expired' pada UPDATE apa pun (memblok terima/konversi/
    Perpanjang). Pemulihan = UPDATE bersyarat status='expired' -> 'sent', SESUDAH semua tulisan quotes."""
    db = pasang(DBU("sent", kepala_sesudah={**KEPALA, "notes": "z"}))
    await Q.update_quote(_req(), QUOTE_ID, UpdateQuoteRequest(notes="z"))
    urut = [s for (s, _, _) in db.urut]
    i_pulih = max(i for i, s in enumerate(urut) if s.startswith("UPDATE quotes SET status"))
    assert all(i < i_pulih for i, s in enumerate(urut) if s.startswith("UPDATE quotes") and i != i_pulih)
    import inspect
    src = " ".join(inspect.getsource(Q._ubah_penawaran).split())
    assert "UPDATE quotes SET status = 'sent' WHERE id = $1 AND tenant_id = $2 AND status = 'expired'" in src
