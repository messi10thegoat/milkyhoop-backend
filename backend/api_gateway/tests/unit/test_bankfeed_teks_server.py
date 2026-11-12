"""BF-1e (tambahan FE U1): teks milik SERVER + satu bentuk dokumen. total.masuk_tampil/keluar_tampil · tebakan.keyakinan_text/dasar_text · daftar.dokumen_tertaut[] · dokumen
(tebakan, tak-di-bank, tertaut) SATU bentuk {jenis(kunci stabil), jenis_text, sumber, id(dokumen), nomor, bt_id}. Aditif, tanpa jurnal. TES MERAH DULU (dijalankan sesudah syarat server lepas)."""
import ast
import asyncio
import inspect
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from app.services import bankfeed_daftar as BD
from app.services import bankfeed_dokumen as BDK
from app.services import bankfeed_tak_di_bank as TD
from app.services import bankfeed_tebak as TB
from app.services import kasbank_ringkas as KR
from .test_bankfeed_catat3 import REK, T

BENTUK = {"jenis", "jenis_text", "sumber", "id", "nomor", "bt_id"}


# ====================================================================== bentuk dokumen
@pytest.mark.parametrize("sumber,kunci,label", [("BILL_PAYMENT", "pembayaran_tagihan", "Bayar tagihan"), ("PAYMENT_MADE", "pembayaran_tagihan", "Bayar tagihan"),
                                                ("RECEIVE_PAYMENT", "penerimaan_pembayaran", "Terima pembayaran"), ("CUSTOMER_DEPOSIT", "uang_muka_pelanggan", "Uang muka pelanggan"),
                                                ("VENDOR_DEPOSIT", "uang_muka_vendor", "Uang muka vendor"), ("EXPENSE", "biaya", "Biaya"), ("BANK_TRANSFER", "transfer", "Transfer"),
                                                ("BANK_TRANSACTION", "mutasi_manual", "Manual")])
def test_bentuk_dokumen_satu_bentuk_kunci_stabil_dan_label_server(sumber, kunci, label):
    d, b = uuid.uuid4(), uuid.uuid4()
    h = BDK.bentuk(sumber, d, "NO-1", b)
    assert set(h) == BENTUK and h == {"jenis": kunci, "jenis_text": label, "sumber": sumber, "id": str(d), "nomor": "NO-1", "bt_id": str(b)}


def test_sumber_tak_dikenal_jadi_lainnya_dengan_label_jurnal_dan_tanpa_dokumen_id():
    h = BDK.bentuk("SESUATU_BARU", None, None, uuid.uuid4())
    assert h["jenis"] == "lainnya" and h["jenis_text"] == KR.LABEL_LAIN and h["id"] is None and h["nomor"] is None and h["sumber"] == "SESUATU_BARU"


def test_semua_kunci_sumber_punya_label_server():
    assert set(BDK.KUNCI_SUMBER) <= set(KR.LABEL_SUMBER)


# ====================================================================== tebakan: teks keyakinan + dasar, dokumen satu bentuk
def test_ringkas_tebakan_membawa_keyakinan_text_dan_dasar_text_manusia():
    t = TB.ringkas_tebakan({**TB._kosong(), "keyakinan": "yakin", "dasar": "bawaan_bca", "layak_massal": False, "layak_massal_text": None})
    assert t["keyakinan_text"] == "Yakin" and t["dasar_text"] == "Pola keterangan bank BCA" and t["dasar_text"] != t["dasar"]
    k = TB.ringkas_tebakan({**TB._kosong(), "layak_massal": False, "layak_massal_text": None})
    assert k["keyakinan_text"] == "Belum ada tebakan" and k["dasar_text"] is None


def test_setiap_keyakinan_dan_dasar_yang_dihasilkan_penentu_punya_teks():
    pohon = ast.parse(inspect.getsource(TB))
    dasar = {kw.value.value for n in ast.walk(pohon) if isinstance(n, ast.Call) for kw in n.keywords
             if kw.arg == "dasar" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)}
    keyakinan = {kw.value.value for n in ast.walk(pohon) if isinstance(n, ast.Call) for kw in n.keywords
                 if kw.arg == "keyakinan" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)}
    assert dasar and dasar <= set(TB.DASAR_TEKS), dasar - set(TB.DASAR_TEKS)
    assert (keyakinan | {"kurang_data"}) <= set(TB.KEYAKINAN_TEKS), (keyakinan | {"kurang_data"}) - set(TB.KEYAKINAN_TEKS)


def test_tebakan_dokumen_memakai_bentuk_bersama_dengan_id_dokumen_bukan_id_bank():
    d = uuid.uuid4()
    h = TB._dokumen([{"id": "b1", "sumber": "RECEIVE_PAYMENT", "dokumen_id": d, "nomor": "RCV-1", "jenis": "payment_received"}])
    assert h == [BDK.bentuk("RECEIVE_PAYMENT", d, "RCV-1", "b1")] and h[0]["id"] == str(d) and h[0]["bt_id"] == "b1"


def test_tak_di_bank_dokumen_memakai_bentuk_bersama():
    assert "BDK.bentuk(" in inspect.getsource(TD)


# ====================================================================== daftar: total tampil + dokumen_tertaut
class _KD:
    def __init__(self, tertaut):
        self.sql, self.tertaut = [], tertaut
        self.ids = [uuid.uuid4(), uuid.uuid4()]

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if sql.startswith("SELECT 1 FROM bank_accounts"):
            return {"x": 1}
        if "COALESCE(SUM(nominal)" in sql:
            return {"masuk": D("1250000.75"), "keluar": D(300000), "n": 2}
        raise AssertionError(sql[:80])

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "GROUP BY status" in sql:
            return []
        if "FROM mutasi_bank_tautan t" in sql:
            return self.tertaut(self.ids)
        if "ORDER BY tanggal DESC" in sql:
            dasar = {"tanggal": date(2026, 10, 10), "keterangan": "TRSF UJI", "diputus_oleh": None, "rekonsiliasi_id": None, "nominal": D(100000)}
            return [{"id": self.ids[0], "arah": "masuk", "status": "untuk_ditinjau", "cara": None, "diputus_pada": None, **dasar},
                    {"id": self.ids[1], "arah": "keluar", "status": "dicatat", "cara": "cocok", "diputus_pada": datetime(2026, 11, 12, tzinfo=timezone.utc), **dasar}]
        raise AssertionError(sql[:80])


@pytest.fixture
def _dasar(monkeypatch):
    async def kosong(*a, **k):
        return {}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(BD.TB, "tebak_halaman", kosong)
    monkeypatch.setattr(BD, "nama_untuk", kosong)
    monkeypatch.setattr(BD, "zona_tenant", zona)


def test_daftar_total_membawa_masuk_tampil_dan_keluar_tampil(_dasar):
    k = _KD(lambda ids: [])
    h = asyncio.run(BD.daftar(k, T, REK, None, None, None, None, 50, 0))
    assert h["total"]["masuk"] == "1250000.75" and h["total"]["masuk_tampil"] == "Rp 1.250.000,75" and h["total"]["keluar_tampil"] == "Rp 300.000" and h["total"]["jumlah"] == 2


def test_daftar_dokumen_tertaut_hanya_baris_dicatat_satu_query_satu_bentuk(_dasar):
    doc = uuid.uuid4()
    k = _KD(lambda ids: [{"mutasi_bank_id": ids[1], "bt_id": uuid.uuid4(), "sumber": "BILL_PAYMENT", "dokumen_id": doc, "nomor": "BP-2611-0001"}])
    h = asyncio.run(BD.daftar(k, T, REK, None, None, None, None, 50, 0))
    ditinjau, dicatat = h["data"]
    assert ditinjau["dokumen_tertaut"] == [] and len(dicatat["dokumen_tertaut"]) == 1
    d = dicatat["dokumen_tertaut"][0]
    assert set(d) == BENTUK and d["jenis"] == "pembayaran_tagihan" and d["jenis_text"] == "Bayar tagihan" and d["id"] == str(doc) and d["nomor"] == "BP-2611-0001"
    q = [(s, a) for s, a in k.sql if "FROM mutasi_bank_tautan t" in s]
    assert len(q) == 1 and "tenant_id = $1" in q[0][0] and "dilepas_pada IS NULL" in q[0][0] and q[0][1][0] == T and q[0][1][1] == [k.ids[1]]


def test_tanpa_baris_dicatat_tak_ada_query_tautan(_dasar):
    class _K2(_KD):
        async def fetch(self, sql, *a):
            if "ORDER BY tanggal DESC" in sql:
                self.sql.append((sql, a))
                return [dict((await super().fetch(sql, *a))[0])]
            return await super().fetch(sql, *a)
    k = _K2(lambda ids: [])
    asyncio.run(BD.daftar(k, T, REK, None, None, None, None, 50, 0))
    assert not any("FROM mutasi_bank_tautan t" in s for s, _ in k.sql)
