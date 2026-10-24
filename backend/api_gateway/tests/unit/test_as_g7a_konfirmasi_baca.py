"""AS-G7a (24 Okt, desain disetujui MASTER): pratinjau konfirmasi SO = penentu bersama BACA SAJA.
- rencana_konfirmasi_so: SATU penentu (404/400) untuk penulis (FOR UPDATE) dan pratinjau (tanpa kunci).
- kode_order.perkiraan: kode yang AKAN terbit tanpa kunci advisory/penghitung; aturan lompat = _kode_terpakai (sama dengan penerbit).
- Massal: 'dipesan' membuat kode item berikutnya berurutan persis tulis berurutan.
Koneksi tiruan MENOLAK tulisan/kunci apa pun pada jalur pratinjau."""
import inspect
import re
import uuid
from datetime import date

import pytest
from fastapi import HTTPException

from app.routers import bulk_so as BS
from app.routers import sales_orders as SO
from app.services import kode_order as KO

T = "t-uji"
SOID = str(uuid.uuid4())
NYALA = {**KO.BAWAAN, "enabled": True, "template": "{SEQ}-{MM}-{YY}", "min_digits": 3, "reset": "monthly",
         "trigger": "so_confirmed", "allow_override": True}
TGL = date(2026, 10, 24)


class _BacaConn:
    """Pratinjau: hanya SELECT. Tulisan / kunci = AssertionError (kontrol: jalur yang menulis pasti meledak di sini)."""

    def __init__(self, setelan=NYALA, so=None, last_seq=5, terpakai=(), status="draft"):
        self.setelan, self.last_seq, self.terpakai, self.sql = setelan, last_seq, set(terpakai), []
        self.so = so if so is not None else {"id": uuid.UUID(SOID), "status": status, "order_number": "SO-1", "order_code": None}

    def _jaga(self, q):
        self.sql.append(q)
        for kata in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE", "pg_advisory"):
            if kata in q:
                raise AssertionError(f"pratinjau menulis/mengunci: {q[:80]}")

    async def fetchrow(self, q, *a):
        self._jaga(q)
        if "FROM order_code_settings" in q:
            return self.setelan
        if "FROM sales_orders" in q:
            return self.so
        raise AssertionError(q)

    async def fetchval(self, q, *a):
        self._jaga(q)
        if "FROM order_code_counters" in q:
            return self.last_seq
        if "order_code = $2" in q:
            return 1 if a[1] in self.terpakai else None
        raise AssertionError(q)

    async def execute(self, q, *a):
        self._jaga(q)


CTX = {"tenant_id": T, "user_id": "u1"}


@pytest.fixture(autouse=True)
def tanggal(monkeypatch):
    async def tgl(conn, tid):
        return TGL
    monkeypatch.setattr(SO, "tanggal_dokumen", tgl)


@pytest.mark.asyncio
async def test_perkiraan_kode_berikut_tanpa_tulis_dan_lompat_terpakai():
    assert await KO.perkiraan(_BacaConn(last_seq=5), T, {"order_code": None}, TGL, "so_confirmed") == "006-10-26"
    assert await KO.perkiraan(_BacaConn(last_seq=None), T, {"order_code": None}, TGL, "so_confirmed") == "001-10-26"
    assert await KO.perkiraan(_BacaConn(last_seq=5, terpakai={"006-10-26", "007-10-26"}), T, {"order_code": None}, TGL,
                              "so_confirmed") == "008-10-26"


@pytest.mark.asyncio
async def test_perkiraan_none_sama_syarat_dengan_terbitkan():
    assert await KO.perkiraan(_BacaConn(), T, {"order_code": "001-10-26"}, TGL, "so_confirmed") is None
    assert await KO.perkiraan(_BacaConn(setelan={**NYALA, "enabled": False}), T, {"order_code": None}, TGL, "so_confirmed") is None
    assert await KO.perkiraan(_BacaConn(setelan={**NYALA, "trigger": "manual_only"}), T, {"order_code": None}, TGL,
                              "so_confirmed") is None


class _TulisConn:
    """Penerbit nyata (_terbitkan_inti) atas keadaan yang sama -> pembanding paritas perkiraan == terbit."""

    def __init__(self, last_seq, terpakai):
        self.last_seq, self.terpakai, self.so = last_seq, set(terpakai), {"id": 1, "order_number": "SO-1", "order_code": None}

    async def execute(self, q, *a):
        return "UPDATE 1"

    async def fetchrow(self, q, *a):
        return self.so

    async def fetchval(self, q, *a):
        if "RETURNING last_seq" in q:
            self.last_seq = (self.last_seq or 0) + 1
            return self.last_seq
        if "order_code = $2" in q:
            return 1 if a[1] in self.terpakai else None
        raise AssertionError(q)


@pytest.mark.asyncio
@pytest.mark.parametrize("seq,terpakai", [(None, ()), (5, ()), (5, {"006-10-26"}), (9, {"010-10-26", "011-10-26"})])
async def test_paritas_perkiraan_sama_dengan_penerbit(monkeypatch, seq, terpakai):
    async def catat(*a, **k):
        return None
    monkeypatch.setattr(KO, "_catat", catat)
    kira = await KO.perkiraan(_BacaConn(last_seq=seq, terpakai=terpakai), T, {"order_code": None}, TGL, "so_confirmed")
    terbit = await KO._terbitkan_inti(_TulisConn(seq, terpakai), T, 1, TGL, NYALA, "auto", None, {})
    assert kira == terbit


def test_aturan_lompat_satu_fungsi_dipakai_penerbit_dan_perkiraan():
    for fn in (KO._terbitkan_inti, KO.perkiraan):
        src = inspect.getsource(fn)
        assert "_kode_terpakai(" in src and "order_code = $2" not in src, fn.__name__


@pytest.mark.asyncio
async def test_pratinjau_konfirmasi_baca_saja_membawa_perkiraan(monkeypatch):
    async def st(conn, tid):
        return NYALA
    monkeypatch.setattr(KO, "muat_setelan", st)
    c = _BacaConn(last_seq=5)
    h = await SO.pratinjau_konfirmasi_so(c, CTX, SOID)
    assert h == {"order_number": "SO-1", "status": "confirmed", "order_code": "006-10-26", "order_code_issued": True,
                 "order_code_label": NYALA["label"], "order_title_label": NYALA["title_label"], "order_code_estimated": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("so,status,kode", [({}, None, 404), (None, "confirmed", 400)])
async def test_pratinjau_galat_sama_dengan_penulis(so, status, kode):
    c = _BacaConn(so=so if so is not None else None, status=status or "draft")
    if so == {}:
        c.so = None
    with pytest.raises(HTTPException) as e:
        await SO.pratinjau_konfirmasi_so(c, CTX, SOID)
    assert e.value.status_code == kode


PENULIS = re.compile(r"(?<![A-Za-z_])_konfirmasi_so\(")  # panggilan PENULIS; 'pratinjau_konfirmasi_so(' bukan


def test_pola_penulis_bisa_gagal():
    """Kontrol: pola menangkap panggilan penulis, tak tertipu nama pratinjau (dulu substring -> positif palsu)."""
    assert PENULIS.search("h = await _konfirmasi_so(conn, ctx, oid)") and PENULIS.search("x = SO._konfirmasi_so(c)")
    assert not PENULIS.search("h = await pratinjau_konfirmasi_so(conn, ctx, oid)")


def test_penulis_memakai_penentu_berkunci_sebelum_update_dan_kode():
    src = inspect.getsource(SO._konfirmasi_so)
    i_r = src.index("rencana_konfirmasi_so(conn, ctx, order_id, kunci=True)")
    assert i_r < src.index("UPDATE sales_orders SET status = 'confirmed'") < src.index("_terbitkan_kode(")
    assert "AND status = 'draft'" in src  # UPDATE bersyarat tetap (sabuk pengaman)
    pr = inspect.getsource(SO.pratinjau_konfirmasi_so)
    assert "kunci=False" in pr and not PENULIS.search(pr) and "_perkiraan_kode(" in pr
    assert '" FOR UPDATE" if kunci else ""' in inspect.getsource(SO.rencana_konfirmasi_so)


def test_rute_pratinjau_read_only_tanpa_penulis():
    src = inspect.getsource(SO.preview_confirm_sales_order)
    assert "conn.transaction(readonly=True)" in src and "pratinjau_konfirmasi_so(" in src and not PENULIS.search(src)


@pytest.mark.asyncio
async def test_massal_pratinjau_kode_berurutan_tanpa_tulis(monkeypatch):
    async def st(conn, tid):
        return NYALA
    monkeypatch.setattr(KO, "muat_setelan", st)
    ctx = dict(CTX)
    c = _BacaConn(last_seq=5, terpakai={"007-10-26"})
    hasil = [(await BS._pratinjau_konfirmasi(c, ctx, uuid.uuid4()))[1]["order_code"] for _ in range(3)]
    assert not PENULIS.search(inspect.getsource(BS._pratinjau_konfirmasi))
    assert hasil == ["006-10-26", "008-10-26", "009-10-26"]  # 007 terpakai dilompati, tiap item nomor sesudahnya


def test_kontrol_merah_penjaga_tiruan_menangkap_jalur_berkunci():
    """Kontrol: koneksi tiruan pratinjau MEMANG meledak bila jalur memakai FOR UPDATE / advisory / tulisan."""
    import asyncio
    c = _BacaConn()
    with pytest.raises(AssertionError):
        asyncio.run(SO.rencana_konfirmasi_so(c, CTX, SOID, kunci=True))
    with pytest.raises(AssertionError):
        asyncio.run(c.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "x"))
