"""V421 (14 Okt 2026, W-20261014-QUOTE-EXPIRY-TURUNAN; kontrak MASTER): kedaluwarsa penawaran = TURUNAN tanggal
(services/penawaran_kedaluwarsa, tanggal bisnis tenant), BUKAN status tersimpan. trg_quote_expiry dicabut (0 baris
'expired' saat cabut). Dijaga di sini: (1) tak ada SQL tulis quotes yang menyebut 'expired' + migrasi mencabut trigger
dan tak ada yang memasangnya lagi; (2) /send lewat masa -> 409 QUOTE_NOT_SHAREABLE nol tulis (penentu SAMA dengan
bagikan); (3) ?status=expired dan summary expired_count = predikat turunan. Koneksi tiruan, tanpa DB."""
import ast
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import quotes as Q
from app.services import penawaran_bagikan as PB
from app.services import penawaran_kedaluwarsa as PK

APP = Path(Q.__file__).resolve().parents[1]
H = date(2026, 10, 14)
TENANT = "kaos-biru-konveksi"


def _migrasi():
    for d in (APP.parents[1] / "migrations", Path("/app/backend/migrations")):
        if d.is_dir():
            return d
    pytest.skip("folder migrasi tak terpasang")


# ---- (1) penjaga: tak ada penulis status 'expired' ----

def _teks_sql(path: Path):
    """Semua literal string (termasuk potongan f-string) di berkas -> [(baris, teks)]."""
    pohon = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(pohon):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.lineno, n.value
        elif isinstance(n, ast.JoinedStr):
            yield n.lineno, "".join(v.value for v in n.values if isinstance(v, ast.Constant))


_TULIS_QUOTES = re.compile(r"\b(UPDATE\s+quotes|INSERT\s+INTO\s+quotes)\b", re.I)


def test_tak_ada_sql_tulis_quotes_yang_menyebut_expired():
    langgar = [f"{p.relative_to(APP)}:{ln}" for p in APP.rglob("*.py") for ln, s in _teks_sql(p)
               if _TULIS_QUOTES.search(s) and "'expired'" in s]
    assert langgar == [], langgar


def test_penjaga_sql_bisa_gagal():
    """Kontrol alat: pemulih lama (dicabut V421) WAJIB tertangkap pola yang sama."""
    lama = "UPDATE quotes SET status = 'sent' WHERE id = $1 AND tenant_id = $2 AND status = 'expired'"
    assert _TULIS_QUOTES.search(lama) and "'expired'" in lama


def test_v421_mencabut_trigger_dan_tak_ada_migrasi_sesudahnya_memasang_lagi():
    d = _migrasi()
    [v421] = [p for p in d.glob("V421__*.sql") if "ROLLBACK" not in p.name]
    isi = v421.read_text()
    assert "DROP TRIGGER IF EXISTS trg_quote_expiry ON quotes;" in isi
    assert "DROP FUNCTION IF EXISTS check_quote_expiry();" in isi
    for p in d.glob("V*__*.sql"):
        m = re.match(r"V(\d+)__", p.name)
        if m and int(m.group(1)) > 421 and "ROLLBACK" not in p.name:
            assert "check_quote_expiry" not in p.read_text() and "trg_quote_expiry" not in p.read_text(), p.name


def test_status_tersimpan_expired_tak_lagi_diperlakukan_khusus():
    assert "expired" not in Q.STATUS_BOLEH_UBAH
    assert "expired" not in PB.TAK_BOLEH
    import inspect
    assert '"expired"' not in inspect.getsource(PB.penentu)


# ---- (2) /send: penentu yang sama dengan bagikan ----

QID = uuid.uuid4()


class KirimConn:
    def __init__(self, status, expiry):
        self.status, self.expiry, self.tulis = status, expiry, []

    async def execute(self, q, *a):
        s = " ".join(q.split())
        if not s.startswith("SELECT pg_advisory"):
            self.tulis.append(s)
        return "UPDATE 1"

    async def fetchrow(self, q, *a):
        s = " ".join(q.split())
        assert s.startswith("SELECT id, status, quote_number, expiry_date FROM quotes") and "FOR UPDATE" in s, s
        assert a == (QID, TENANT)
        return {"id": QID, "status": self.status, "quote_number": "QUO-2610-0003", "expiry_date": self.expiry}


@pytest.fixture
def kirim(monkeypatch):
    async def hari(c, t):
        return H
    monkeypatch.setattr(Q, "tanggal_dokumen", hari)
    from app.services import so_riwayat
    riwayat = []

    async def catat(*a, **k):
        riwayat.append(a[5])
    monkeypatch.setattr(so_riwayat, "catat_riwayat", catat)
    return riwayat


_CTX = {"tenant_id": TENANT, "user_id": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["sent", "draft"])
async def test_kirim_lewat_masa_409_nol_tulis_pesan_menunjuk_ubah_tanggal(kirim, status):
    c = KirimConn(status, H - timedelta(days=1))
    with pytest.raises(HTTPException) as e:
        await Q.kirim_penawaran_core(c, _CTX, QID)
    assert e.value.status_code == 409 and e.value.detail["code"] == "QUOTE_NOT_SHAREABLE"
    m = e.value.detail["message"]
    assert "13/10/2026" in m and "Ubah tanggal berlaku dulu." in m and "QUO-2610-0003" in m, m
    assert c.tulis == [] and kirim == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exp", [("sent", H), ("sent", None), ("draft", H + timedelta(days=7))])
async def test_kirim_masih_berlaku_ditandai_terkirim(kirim, status, exp):
    c = KirimConn(status, exp)
    r = await Q.kirim_penawaran_core(c, _CTX, QID)
    assert r == {"quote_number": "QUO-2610-0003", "status": "sent"}
    assert [s[:26] for s in c.tulis] == ["UPDATE quotes SET status ="] and kirim == ["QUOTE_SENT"]


@pytest.mark.asyncio
async def test_kirim_massal_meneruskan_kode_409_apa_adanya(kirim):
    from app.routers import bulk_quote as BQ
    with pytest.raises(HTTPException) as e:
        await BQ._kirim(KirimConn("sent", H - timedelta(days=3)), _CTX, QID)
    assert e.value.status_code == 409 and e.value.detail["code"] == "QUOTE_NOT_SHAREABLE"


# ---- (3) daftar ?status=expired + summary expired_count = turunan ----

class Conn:
    def __init__(self):
        self.kueri = []

    @staticmethod
    def _cek(sql, a):
        n = {int(x) for x in re.findall(r"\$(\d+)", sql)}
        assert n == set(range(1, len(a) + 1)), (sorted(n), len(a), sql)

    async def fetchval(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        return 0

    async def fetch(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        return []

    async def fetchrow(self, sql, *a):
        self._cek(sql, a)
        self.kueri.append((sql, a))
        return {k: 0 for k in re.findall(r"\bas (\w+)", sql)}


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


@pytest.fixture
def pasang(monkeypatch):
    def _p(conn):
        async def gp():
            return Pool(conn)

        async def hari(c, t):
            return H

        async def so_hasil(c, t, ids):
            return {}
        monkeypatch.setattr(Q, "get_pool", gp)
        monkeypatch.setattr(Q, "tanggal_dokumen", hari)
        from app.services import kode_order
        monkeypatch.setattr(kode_order, "so_hasil_penawaran", so_hasil)
        return conn
    return _p


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": None, "tenant_id": TENANT}))


@pytest.mark.asyncio
@pytest.mark.parametrize("cari", [None, "kaos"])
async def test_daftar_status_expired_alias_predikat_turunan(pasang, cari):
    c = pasang(Conn())
    await Q.list_quotes(_req(), status="expired", aksi=None, customer_id=None, start_date=None, end_date=None,
                        search=cari, skip=0, limit=20, page=1, offset=0)
    assert len(c.kueri) >= 2
    for sql, a in c.kueri[:2]:
        assert PK.sql_kedaluwarsa("$2") in sql and a[1] == H, sql
        assert "status = $" not in sql and "'expired'" not in sql


@pytest.mark.asyncio
async def test_daftar_status_expired_bertumpuk_dengan_aksi(pasang):
    c = pasang(Conn())
    await Q.list_quotes(_req(), status="expired", aksi="lewat_masa", customer_id=None, start_date=None, end_date=None,
                        search=None, skip=0, limit=20, page=1, offset=0)
    sql, a = c.kueri[0]
    assert PK.sql_kedaluwarsa("$2") in sql and a[1] == H


@pytest.mark.asyncio
async def test_summary_expired_count_turunan(pasang):
    c = pasang(Conn())
    await Q.get_quote_summary(_req())
    sql, a = c.kueri[-1]
    assert f"COUNT(*) FILTER (WHERE {PK.sql_kedaluwarsa('$2')}) as expired_count" in sql and a[1] == H
    assert "status = 'expired'" not in sql
