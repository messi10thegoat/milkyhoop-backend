"""Pill AKSI daftar Penawaran (13 Okt 2026, W-20261013-QUOTE-PILL-AKSI; putusan pemilik, pola NetSuite "Pending …").

Server menentukan keanggotaan (FE tak menghitung tanggal): ?aksi= di GET /quotes, `aksi` per baris, *_count/*_value
di /summary -- SATU aturan (services/penawaran_kedaluwarsa.aksi_penawaran / sql_aksi), tanggal BISNIS tenant.
Kesetaraan Python<->SQL di data nyata dibuktikan harness scripts/ops/uji-nyata-ubah-quote.py."""
import re
import typing
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.routers import quotes as Q
from app.schemas.quotes import QuoteSummary
from app.services import penawaran_kedaluwarsa as PK

H = date(2026, 10, 13)
TENANT = "kaos-biru-konveksi"


@pytest.mark.parametrize("status,exp,konv,harap", [
    ("draft", None, None, "perlu_dikirim"),
    ("draft", H - timedelta(days=30), None, "perlu_dikirim"),        # draf lewat tanggal tetap "perlu dikirim"
    ("sent", None, None, "tunggu_jawaban"),                          # tanpa tanggal berlaku = tak pernah habis
    ("sent", H + timedelta(days=4), None, "tunggu_jawaban"),         # sisa 4 > 3
    ("viewed", H + timedelta(days=3), None, "segera_habis"),         # sisa 3 = ambang -> segera
    ("sent", H, None, "segera_habis"),                               # habis HARI INI = belum lewat (Q-017)
    ("sent", H - timedelta(days=1), None, "lewat_masa"),
    ("viewed", H - timedelta(days=9), None, "lewat_masa"),
    ("accepted", H - timedelta(days=9), None, "perlu_dipesan"),
    ("accepted", None, "x", None),                                   # sudah punya dokumen hasil
    ("converted", None, "x", None),
    ("declined", None, None, None),
    ("void", None, None, None),
    ("expired", H - timedelta(days=1), None, None),                  # status tersimpan (trigger), bukan 'menunggu'
])
def test_aturan_aksi(status, exp, konv, harap):
    assert PK.aksi_penawaran(status, exp, H, konv) == harap


def test_lewat_masa_sama_dengan_is_expired_q017():
    for st in ("draft", "sent", "viewed", "accepted", "expired"):
        for d in (-5, -1, 0, 1, 3, 4, None):
            exp = None if d is None else H + timedelta(days=d)
            assert (PK.aksi_penawaran(st, exp, H) == "lewat_masa") is PK.kedaluwarsa(st, exp, H), (st, d)


def test_ambang_bawaan_tiga_dan_bisa_diganti():
    assert PK.AMBANG_SEGERA_HABIS == 3
    assert PK.aksi_penawaran("sent", H + timedelta(days=5), H, ambang=7) == "segera_habis"


def test_sql_aksi_terkunci_dan_satu_per_kunci():
    assert set(PK.AKSI) == {"perlu_dikirim", "tunggu_jawaban", "segera_habis", "lewat_masa", "perlu_dipesan"}
    s = {k: PK.sql_aksi(k, "$2", "$3") for k in PK.AKSI}
    assert s["perlu_dikirim"] == "(status = 'draft')"
    assert s["tunggu_jawaban"] == "(status IN ('sent', 'viewed') AND (expiry_date IS NULL OR expiry_date > ($2::date + $3::int)))"
    assert s["segera_habis"] == "(status IN ('sent', 'viewed') AND expiry_date >= $2::date AND expiry_date <= ($2::date + $3::int))"
    assert s["lewat_masa"] == PK.sql_kedaluwarsa("$2")
    assert s["perlu_dipesan"] == "(status = 'accepted' AND converted_to_id IS NULL)"
    with pytest.raises(KeyError):
        PK.sql_aksi("tak_ada", "$2", "$3")


def test_param_aksi_daftar_sama_dengan_AKSI():
    import inspect
    ann = inspect.signature(Q.list_quotes).parameters["aksi"].annotation
    lit = [a for a in typing.get_args(ann) if a is not type(None)][0]
    assert tuple(typing.get_args(lit)) == PK.AKSI


# ---- daftar & ringkasan di atas koneksi palsu yang menolak penomoran $N yang salah ----

class Conn:
    def __init__(self, baris=()):
        self.baris = list(baris)
        self.kueri = []

    @staticmethod
    def _cek(sql, a):
        n = {int(x) for x in re.findall(r"\$(\d+)", sql)}
        assert n == set(range(1, len(a) + 1)), (sorted(n), len(a), sql)

    async def fetchval(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        return len(self.baris)

    async def fetch(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        return self.baris

    async def fetchrow(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        nama = re.findall(r"\bas (\w+)", sql)
        return {k: 0 for k in nama}


class Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class A:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return A()


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": None, "tenant_id": TENANT}))


@pytest.fixture
def pasang(monkeypatch):
    def _p(conn):
        async def gp():
            return Pool(conn)

        async def hari(c, t):
            return H

        async def so_hasil(c, t, ids):
            return {str(i): {} for i in ids}
        monkeypatch.setattr(Q, "get_pool", gp)
        monkeypatch.setattr(Q, "tanggal_dokumen", hari)
        from app.services import kode_order
        monkeypatch.setattr(kode_order, "so_hasil_penawaran", so_hasil)
        return conn
    return _p


def _row(i, status, exp, konv=None):
    return {"id": f"00000000-0000-0000-0000-00000000000{i}", "quote_number": f"Q{i}", "quote_date": H, "expiry_date": exp,
            "customer_id": "c", "customer_name": "C", "subject": None, "subtotal": 1, "discount_amount": 0,
            "tax_amount": 0, "total_amount": 1, "dp_amount": None, "dp_percent": None, "status": status,
            "converted_to_type": None, "converted_to_id": konv, "created_at": H}


@pytest.mark.asyncio
@pytest.mark.parametrize("aksi", list(PK.AKSI))
async def test_daftar_aksi_memasang_predikat_dan_parameter(pasang, aksi):
    c = pasang(Conn())
    await Q.list_quotes(_req(), status="all", aksi=aksi, customer_id=None, start_date=None, end_date=None,
                        search="kaos", skip=0, limit=20, page=1, offset=0)
    hitung, daftar = c.kueri[0], c.kueri[1]
    pred, par = PK.sql_aksi_berparam(aksi, 2, H)
    for sql, a in (hitung, daftar):
        assert pred in sql, sql
        assert list(a[1:1 + len(par)]) == par


@pytest.mark.asyncio
async def test_daftar_tanpa_aksi_tak_menyaring(pasang):
    c = pasang(Conn())
    await Q.list_quotes(_req(), status="all", aksi=None, customer_id=None, start_date=None, end_date=None,
                        search=None, skip=0, limit=20, page=1, offset=0)
    assert all("expiry_date <=" not in s and "status = 'draft'" not in s for s, _ in c.kueri)


@pytest.mark.asyncio
async def test_tiap_baris_membawa_aksi(pasang):
    pasang(Conn([_row(1, "draft", None), _row(2, "sent", H + timedelta(days=2)), _row(3, "sent", H - timedelta(days=1)),
                 _row(4, "accepted", None), _row(5, "void", None), _row(6, "viewed", H + timedelta(days=10))]))
    r = await Q.list_quotes(_req(), status="all", aksi=None, customer_id=None, start_date=None, end_date=None,
                            search=None, skip=0, limit=20, page=1, offset=0)
    assert [i.aksi for i in r.items] == ["perlu_dikirim", "segera_habis", "lewat_masa", "perlu_dipesan", None,
                                         "tunggu_jawaban"]
    assert [i.is_expired for i in r.items] == [False, False, True, False, False, False]


@pytest.mark.asyncio
async def test_ringkasan_lima_aksi_count_value(pasang):
    c = pasang(Conn())
    r = await Q.get_quote_summary(_req())
    sql, a = c.kueri[-1]
    assert a[1] == H and a[2] == PK.AMBANG_SEGERA_HABIS
    for k in PK.AKSI:
        pred = PK.sql_aksi(k, "$2", "$3")
        assert f"COUNT(*) FILTER (WHERE {pred}) as {k}_count" in sql
        assert f"COALESCE(SUM(total_amount) FILTER (WHERE {pred}), 0) as {k}_value" in sql
        assert f"{k}_count" in r.data.model_fields_set and f"{k}_value" in r.data.model_fields_set
    assert "pending_active_count" in r.data.model_fields_set  # medan Q-017 tetap


def test_skema_ringkasan_punya_sepuluh_medan_baru():
    f = QuoteSummary.model_fields
    for k in PK.AKSI:
        assert f"{k}_count" in f and f"{k}_value" in f


@pytest.mark.parametrize("aksi", list(PK.AKSI))
def test_berparam_hanya_parameter_yang_dirujuk(aksi):
    pred, par = PK.sql_aksi_berparam(aksi, 5, H)
    n = sorted({int(x) for x in re.findall(r"\$(\d+)", pred)})
    assert n == list(range(5, 5 + len(par))), (aksi, n, par)
