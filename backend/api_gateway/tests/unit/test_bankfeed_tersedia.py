"""BF-1e: jenis_tersedia + aksi_tersedia di daftar mutasi (G1) dan perlu_ditinjau per rekening di daftar Kas & Bank (G6). FE TIDAK menyusun daftar jenis/aksi sendiri.
Penentu = fungsi MURNI pada kolom baris (bukan N kali penentu aksi per halaman); KESETARAAN dengan penentu tiap aksi (rencana_*) dikunci tes di bawah. TES MERAH DULU (dijalankan sesudah syarat server lepas)."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from app.services import bankfeed_aksi as BA
from app.services import bankfeed_daftar as BD
from app.services import bankfeed_tersedia as TS
from app.services.bayar_keluar_rencana import Blok
from .test_bankfeed_catat3 import MID, REK, T, _K, _dok, _m, _tt

IZIN_SEMUA = {"ubah": True, "void": True, "jenis": {j: True for j in BA.IZIN_BUAT}}


def _izin(**k):
    d = {"ubah": True, "void": True, "jenis": dict(IZIN_SEMUA["jenis"])}
    for a, b in k.items():
        if a == "jenis":
            d["jenis"].update(b)
        else:
            d[a] = b
    return d


def _kunci(lst):
    return [j["kunci"] for j in lst]


# ====================================================================== jenis_tersedia
def test_jenis_per_arah_urut_tetap_dan_label_dari_server():
    assert _kunci(TS.jenis_tersedia("masuk", IZIN_SEMUA)) == ["terima_pembayaran", "uang_muka_pelanggan", "uang_masuk", "transfer", "pribadi"]
    keluar = TS.jenis_tersedia("keluar", IZIN_SEMUA)
    assert _kunci(keluar) == [j for j in ("bayar_tagihan", "uang_keluar", "transfer", "pribadi") if j not in BA.JENIS_BELUM]
    assert all(x["label"] == BA.LABEL_JENIS[x["kunci"]] for x in TS.jenis_tersedia("masuk", IZIN_SEMUA))


def test_jenis_hanya_yang_dikenal_penulis_catat_satu_sumber():
    for arah in ("masuk", "keluar"):
        assert set(_kunci(TS.jenis_tersedia(arah, IZIN_SEMUA))) <= set(BA.JENIS_CATAT) - set(BA.JENIS_BELUM)


def test_jenis_disaring_izin_c_modul_tujuan():
    h = _kunci(TS.jenis_tersedia("masuk", _izin(jenis={"uang_muka_pelanggan": False})))
    assert "uang_muka_pelanggan" not in h and "terima_pembayaran" in h and "uang_masuk" in h
    h = _kunci(TS.jenis_tersedia("keluar", _izin(jenis={"transfer": False})))
    assert "transfer" not in h and "uang_keluar" in h and "pribadi" in h


def test_tanpa_izin_ubah_bank_feed_tak_ada_jenis():
    assert TS.jenis_tersedia("masuk", _izin(ubah=False)) == [] and TS.jenis_tersedia("keluar", _izin(ubah=False)) == []


# ====================================================================== aksi_tersedia per status
def _row(status, cara=None, rekon=None):
    return {"status": status, "cara": cara, "rekonsiliasi_id": rekon}


def _a(row, izin=IZIN_SEMUA, arah="masuk"):
    return TS.aksi_tersedia(row, TS.jenis_tersedia(arah, izin), izin)


@pytest.mark.parametrize("status", ["untuk_ditinjau", "mungkin_duplikat"])
def test_aksi_baris_ditinjau_cocokkan_catat_kecualikan(status):
    a = _a(_row(status))
    assert set(a) == {"cocokkan", "catat", "kecualikan"} and all(v == {"ok": True, "alasan": None} for v in a.values())


def test_catat_tak_tersedia_berkalimat_bila_tak_ada_jenis_dan_semua_tertutup_tanpa_izin_ubah():
    a = _a(_row("untuk_ditinjau"), _izin(ubah=False))
    assert all(v["ok"] is False and "izin" in v["alasan"].lower() for v in a.values())
    semua_ditolak = _izin(jenis={j: False for j in BA.IZIN_BUAT})
    a = TS.aksi_tersedia(_row("untuk_ditinjau"), [], semua_ditolak)
    assert a["catat"]["ok"] is False and a["catat"]["alasan"] and a["cocokkan"]["ok"] is True


def test_aksi_dicatat_cocok_dan_dikecualikan_hanya_lepaskan():
    for st, cara in (("dicatat", "cocok"), ("dikecualikan", "kecualikan")):
        assert _a(_row(st, cara)) == {"lepaskan": {"ok": True, "alasan": None}}
        a = _a(_row(st, cara, rekon=uuid.uuid4()))
        assert a["lepaskan"]["ok"] is False and "direkonsiliasi" in a["lepaskan"]["alasan"].lower()


@pytest.mark.parametrize("cara", ["catat", "pribadi"])
def test_aksi_dicatat_hasil_catat_batalkan_ya_lepaskan_tidak_dengan_alasan(cara):
    a = _a(_row("dicatat", cara))
    assert a["batalkan_catatan"] == {"ok": True, "alasan": None} and a["lepaskan"]["ok"] is False and "Batalkan catatan" in a["lepaskan"]["alasan"]
    a = _a(_row("dicatat", cara), _izin(void=False))
    assert a["batalkan_catatan"]["ok"] is False and "izin" in a["batalkan_catatan"]["alasan"].lower()
    a = _a(_row("dicatat", cara, rekon=uuid.uuid4()))
    assert a["batalkan_catatan"]["ok"] is False and "direkonsiliasi" in a["batalkan_catatan"]["alasan"].lower()


# ====================================================================== KESETARAAN dengan penentu tiap aksi (satu penentu, dikunci)
KEADAAN = [("untuk_ditinjau", None, None), ("mungkin_duplikat", None, None), ("dicatat", "cocok", None), ("dicatat", "catat", None), ("dicatat", "pribadi", None),
           ("dikecualikan", "kecualikan", None), ("dicatat", "cocok", uuid.uuid4()), ("dikecualikan", "kecualikan", uuid.uuid4())]
BODY = {"cocokkan": {"bank_transaction_ids": [str(uuid.uuid4())]}, "kecualikan": {"alasan": "duplikat"}, "lepaskan": {}, "catat": {"jenis": "uang_masuk", "akun_id": str(uuid.uuid4())},
        "batalkan_catatan": {"alasan": "salah catat"}}
RENCANA = {"cocokkan": "cocokkan", "kecualikan": "kecualikan", "lepaskan": "lepaskan", "catat": "catat", "batalkan_catatan": "batalkan"}


@pytest.mark.parametrize("status,cara,rekon", KEADAAN)
@pytest.mark.parametrize("aksi", list(RENCANA))
def test_aksi_tersedia_setara_dengan_blok_keadaan_penentu_aksi(status, cara, rekon, aksi):
    tersedia = _a(_row(status, cara, rekon)).get(aksi, {"ok": False})["ok"]
    k = _K(_m(status=status, cara=cara, rekonsiliasi_id=rekon, diputus_pada=None), tautan=[_tt()], dok=_dok("BANK_TRANSACTION"))
    try:
        h = asyncio.run(BA.RENCANA[RENCANA[aksi]](k, T, REK, MID, dict(BODY[aksi]), Blok(True), False, None))
        diblok_keadaan = any(b["code"] in TS.KODE_KEADAAN for b in h["blocks"])
    except AssertionError:       # penentu lolos pemeriksaan keadaan dan lanjut ke SQL yang tak disediakan fake -> keadaan TIDAK memblok
        diblok_keadaan = False
    if aksi == "batalkan_catatan" and rekon:
        assert tersedia is False          # penjaga rekonsiliasi di sini lebih ketat (dokumen modul memblok di penentu void; ditampilkan lebih awal)
    else:
        assert tersedia == (not diblok_keadaan), (status, cara, rekon, aksi)


def test_kode_keadaan_adalah_kode_nyata_di_penentu():
    src = inspect.getsource(BA)
    for kode in TS.KODE_KEADAAN:
        assert f'"{kode}"' in src, kode


# ====================================================================== daftar mutasi memakai penentu (bukan []/{} tetap)
class _KD:
    """Fake koneksi untuk BD.daftar: rekening ada, cacah, jumlah, dua baris (masuk untuk_ditinjau, keluar dicatat cara catat)."""
    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if sql.startswith("SELECT 1 FROM bank_accounts"):
            return {"x": 1}
        if "COALESCE(SUM(nominal)" in sql:
            return {"masuk": D(500000), "keluar": D(100000), "n": 2}
        raise AssertionError(sql[:80])

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "GROUP BY status" in sql:
            return [{"status": "untuk_ditinjau", "n": 1}, {"status": "dicatat", "n": 1}]
        if "FROM mutasi_bank WHERE" in sql and "ORDER BY tanggal DESC" in sql:
            from datetime import date, datetime, timezone
            dasar = {"tanggal": date(2026, 10, 10), "keterangan": "TRSF UJI", "diputus_oleh": None, "diputus_pada": None, "rekonsiliasi_id": None}
            return [{"id": uuid.uuid4(), "arah": "masuk", "nominal": D(500000), "status": "untuk_ditinjau", "cara": None, **dasar},
                    {"id": uuid.uuid4(), "arah": "keluar", "nominal": D(100000), "status": "dicatat", "cara": "catat", **dict(dasar, diputus_pada=datetime(2026, 11, 12, tzinfo=timezone.utc))}]
        raise AssertionError(sql[:80])


def test_daftar_mengisi_jenis_dan_aksi_tersedia_per_baris_dan_izin_dihitung_sekali(monkeypatch):
    async def tebak(conn, tid, rid, baris):
        return {}

    async def nama(conn, ids, cadangan_email=True):
        return {}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(BD.TB, "tebak_halaman", tebak)
    monkeypatch.setattr(BD, "nama_untuk", nama)
    monkeypatch.setattr(BD, "zona_tenant", zona)
    panggilan = []

    async def izin(user, rekening_id):
        panggilan.append(rekening_id)
        return IZIN_SEMUA
    monkeypatch.setattr(TS, "izin_pengguna", izin)
    r = asyncio.run(BD.daftar(_KD(), T, REK, None, None, None, None, 50, 0, {"user_id": "u", "tenant_id": T}))
    masuk, keluar = r["data"]
    assert _kunci(masuk["jenis_tersedia"]) == ["terima_pembayaran", "uang_muka_pelanggan", "uang_masuk", "transfer", "pribadi"] and set(masuk["aksi_tersedia"]) == {"cocokkan", "catat", "kecualikan"}
    assert keluar["jenis_tersedia"] == [] and set(keluar["aksi_tersedia"]) == {"batalkan_catatan", "lepaskan"} and keluar["aksi_tersedia"]["batalkan_catatan"]["ok"] is True
    assert panggilan == [REK]


def test_daftar_tanpa_pengguna_tidak_memeriksa_izin_unit_orkestrasi(monkeypatch):
    async def kosong(*a, **k):
        return {}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(BD.TB, "tebak_halaman", kosong)
    monkeypatch.setattr(BD, "nama_untuk", kosong)
    monkeypatch.setattr(BD, "zona_tenant", zona)
    r = asyncio.run(BD.daftar(_KD(), T, REK, None, None, None, None, 50, 0))
    assert r["data"][0]["jenis_tersedia"] and r["data"][0]["aksi_tersedia"]


def test_daftar_tak_lagi_menulis_literal_kosong():
    src = inspect.getsource(BD.daftar)
    assert '"jenis_tersedia": []' not in src and '"aksi_tersedia": {}' not in src and "rekonsiliasi_id" in src


# ====================================================================== G6 perlu_ditinjau per rekening
class _KP:
    def __init__(self):
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return [{"bank_account_id": REK, "n": 3}]


def test_perlu_ditinjau_satu_group_by_untuk_semua_rekening_halaman_tenant_eksplisit():
    k = _KP()
    r = asyncio.run(BD.perlu_ditinjau_per_rekening(k, T, [REK, uuid.uuid4()]))
    assert len(k.sql) == 1 and r[str(REK)] == 3
    sql, a = k.sql[0]
    assert "tenant_id = $1" in sql and "GROUP BY bank_account_id" in sql and "'untuk_ditinjau'" in sql and "'mungkin_duplikat'" in sql and a[0] == T
    assert asyncio.run(BD.perlu_ditinjau_per_rekening(_KP(), T, [])) == {}


def test_daftar_kas_bank_membawa_perlu_ditinjau_per_baris():
    from app.routers import bank_accounts as BAR
    from app.schemas.bank_accounts import BankAccountListItem
    src = inspect.getsource(BAR.list_bank_accounts)
    assert "perlu_ditinjau_per_rekening(" in src and '"perlu_ditinjau"' in src
    assert BankAccountListItem.model_fields["perlu_ditinjau"].default == 0
