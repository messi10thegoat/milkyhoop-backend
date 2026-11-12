"""BF-1c1 laporan 'tercatat di MilkyHoop, tak ada di bank' (KONTRAK-bankfeed §8b + KONTRAK-bf1c1) dan penjaga void 'BANK_TX_BERTAUT' (satu fungsi, semua pemanggil)."""
import ast
import asyncio
import inspect
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.services import bankfeed_tak_di_bank as TD
from app.services import jaga_rekonsiliasi as J

T = "t-uji"
REK = uuid.uuid4()


@pytest.fixture(autouse=True)
def _tanpa_basis_data(monkeypatch):
    async def nama(conn, ids, cadangan_email=True):
        return {str(i): "Sinta" for i in ids}

    async def hari_ini(conn, tid):
        return date(2026, 11, 12)
    monkeypatch.setattr(TD, "nama_untuk", nama)
    monkeypatch.setattr(TD, "tanggal_dokumen", hari_ini)


def _k(id_="b1", tgl=date(2026, 10, 1), nominal="500000.00", arah="masuk", kontak="PT Sumber Rejeki", nomor="RCV-1", jenis="payment_received", sumber="RECEIVE_PAYMENT"):
    return {"id": id_, "tanggal": tgl, "nominal": D(nominal), "arah": arah, "kontak_nama": kontak, "nomor": nomor, "jenis": jenis, "sumber": sumber,
            "kontak_jenis": "pelanggan" if kontak else None, "kontak_id": uuid.uuid4() if kontak else None, "dokumen_id": uuid.uuid4(), "dibuat_oleh": uuid.uuid4(), "dibuat_pada": None}


class _KD:
    def __init__(self, ada=True, rentang=None, lampiran=()):
        self.ada, self.rentang, self.lampiran, self.sql = ada, rentang, list(lampiran), []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM bank_accounts" in sql:
            return {"?column?": 1} if self.ada else None
        if "min(tanggal)" in sql:
            return self.rentang
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "document_attachments" in sql:
            return self.lampiran
        raise AssertionError(sql[:60])


RENTANG = {"awal": date(2026, 8, 1), "akhir": date(2026, 11, 10), "n": 1303}


def _jalan(monkeypatch, kand, dari=None, sampai=None, lampiran=(), rentang=RENTANG):
    panggil = {}

    async def kandidat(conn, tid, rekening_id, d1, d2):
        panggil["args"] = (tid, rekening_id, d1, d2)
        return kand
    monkeypatch.setattr(TD.TB, "kandidat_bt", kandidat)
    h = asyncio.run(TD.daftar(_KD(rentang=rentang, lampiran=lampiran), T, REK, dari, sampai))
    return h, panggil.get("args")


def test_rekening_tak_ada_404():
    with pytest.raises(HTTPException) as e:
        asyncio.run(TD.daftar(_KD(ada=False), T, REK, None, None))
    assert e.value.status_code == 404 and e.value.detail["code"] == "REKENING_TAK_ADA"


def test_belum_ada_berkas_tak_ada_yang_bisa_dinilai(monkeypatch):
    h, args = _jalan(monkeypatch, [_k()], rentang={"awal": None, "akhir": None, "n": 0})
    assert h["data"] == [] and h["rentang_berkas"] is None and args is None and "Belum ada berkas" in h["catatan"][0]


def test_hanya_rentang_berkas_dikurangi_tiga_hari_dinilai_dan_dikatakan(monkeypatch):
    h, args = _jalan(monkeypatch, [], dari=date(2026, 7, 1))
    assert args == (T, REK, date(2026, 8, 1), date(2026, 11, 7))                         # akhir berkas 10 Nov − 3 hari
    assert h["rentang_berkas"] == {"dari": "2026-08-01", "sampai": "2026-11-10"} and h["dinilai"] == {"dari": "2026-08-01", "sampai": "2026-11-07"}
    teks = " ".join(h["catatan"])
    assert "3 hari terakhir tidak dinilai" in teks and "Sebelum 2026-08-01 tidak ada berkas" in teks and "saldo awal" in teks


def test_param_sampai_hanya_menyempitkan_tak_bisa_melewati_batas(monkeypatch):
    _, a1 = _jalan(monkeypatch, [], sampai=date(2026, 12, 31))
    _, a2 = _jalan(monkeypatch, [], dari=date(2026, 9, 1), sampai=date(2026, 10, 1))
    assert a1[3] == date(2026, 11, 7) and (a2[2], a2[3]) == (date(2026, 9, 1), date(2026, 10, 1))


def test_dari_setelah_batas_kosong_tanpa_menyentuh_basis_data(monkeypatch):
    h, args = _jalan(monkeypatch, [_k()], dari=date(2026, 11, 9))
    assert h["data"] == [] and args is None and h["rentang_berkas"]["sampai"] == "2026-11-10"


def test_saldo_awal_dan_penyesuaian_saldo_awal_bukan_transaksi_bank(monkeypatch):
    h, _ = _jalan(monkeypatch, [_k("a", jenis="opening", sumber="BANK_OPENING_BALANCE"), _k("b", sumber="OPENING_BALANCE"), _k("c")])
    assert [x["bt_id"] for x in h["data"]] == ["c"]


def test_bentuk_baris_nominal_tampil_umur_lampiran_dan_pembuat(monkeypatch):
    k = _k("c", tgl=date(2026, 10, 2))
    h, _ = _jalan(monkeypatch, [k], lampiran=[{"entity_id": k["dokumen_id"], "n": 2}])
    x = h["data"][0]
    assert x["nominal_tampil"] == "Rp 500.000" and x["dokumen"] == {"jenis": "penerimaan_pembayaran", "jenis_text": "Terima pembayaran", "sumber": k["sumber"], "id": str(k["dokumen_id"]), "nomor": "RCV-1", "bt_id": str(k["id"])}       # BF-1e: satu bentuk dokumen
    assert x["umur_hari"] == 41 and x["umur_text"] == "41 hari lalu" and x["lampiran"] == {"jumlah": 2} and x["dibuat_oleh_text"] == "Sinta"
    assert x["alasan"] == "tak_ada_di_bank" and x["mungkin_dobel_dengan"] is None and x["kontak"]["nama"] == "PT Sumber Rejeki"


def test_total_masuk_keluar_decimal_dari_server(monkeypatch):
    h, _ = _jalan(monkeypatch, [_k("a", nominal="1000000.50"), _k("b", nominal="250000.25"), _k("c", nominal="300000.00", arah="keluar")])
    assert h["total"] == {"masuk": "1250000.75", "keluar": "300000.00", "jumlah": 3}


def test_mungkin_dobel_kontak_sama_nominal_sama_dalam_tujuh_hari_kasus_ezhra(monkeypatch):
    a, b = _k("a", tgl=date(2026, 10, 5), nomor="DEP-29", kontak="Ezhra"), _k("b", tgl=date(2026, 10, 12), nomor="DEP-37", kontak="Ezhra")
    h, _ = _jalan(monkeypatch, [a, b])
    assert [x["alasan"] for x in h["data"]] == ["mungkin_dobel", "mungkin_dobel"]
    assert h["data"][0]["mungkin_dobel_dengan"] == [{"bt_id": "b", "nomor": "DEP-37", "tanggal": "2026-10-12"}] and "DEP-37" in h["data"][0]["alasan_text"]


@pytest.mark.parametrize("ubah", [{"tgl": date(2026, 10, 13)}, {"kontak": "Budi"}, {"nominal": "500001.00"}, {"arah": "keluar"}])
def test_bukan_dobel_bila_hari_kontak_nominal_atau_arah_beda(monkeypatch, ubah):
    a, b = _k("a", tgl=date(2026, 10, 5), kontak="Ezhra"), _k("b", **{"tgl": date(2026, 10, 12), "kontak": "Ezhra", **ubah})
    h, _ = _jalan(monkeypatch, [a, b])
    assert [x["alasan"] for x in h["data"]] == ["tak_ada_di_bank", "tak_ada_di_bank"]


def test_kontak_kosong_tak_pernah_dianggap_dobel(monkeypatch):
    h, _ = _jalan(monkeypatch, [_k("a", kontak=None), _k("b", kontak=None)])
    assert all(x["alasan"] == "tak_ada_di_bank" for x in h["data"])


def test_laporan_memakai_definisi_kandidat_bersama_dan_semua_sql_bertenant():
    src = inspect.getsource(TD)
    assert "TB.kandidat_bt(" in src and "float(" not in src and "set_config" not in src
    for nama, nilai in vars(TD).items():
        if nama.startswith("SQL_"):
            assert "tenant_id" in nilai, nama
            assert "INSERT" not in nilai.upper() and "UPDATE" not in nilai.upper() and "DELETE" not in nilai.upper(), nama


# ====================================================================== penjaga void: BANK_TX_BERTAUT
class _Conn:
    def __init__(self, **baris):
        self.r, self.q = baris, []

    async def fetchrow(self, q, *a):
        self.q.append((q, a))
        return self.r


def _coba(**baris):
    with pytest.raises(HTTPException) as e:
        asyncio.run(J.tolak_void_bila_terekonsiliasi(_Conn(**baris), T, uuid.uuid4()))
    return e.value


def test_dokumen_bertaut_di_bank_feed_ditolak_409_dengan_jalan_keluar():
    e = _coba(rekon=0, cocok=0, bertaut=1)
    assert e.status_code == 409 and e.detail["code"] == "BANK_TX_BERTAUT" and "Lepaskan dulu di Bank Feed" in e.detail["message"]


def test_urutan_rekon_lalu_bertaut_lalu_cocok_lama():
    assert _coba(rekon=1, cocok=1, bertaut=1).detail["code"] == "BANK_TX_RECONCILED"
    assert _coba(rekon=0, cocok=1, bertaut=1).detail["code"] == "BANK_TX_BERTAUT"
    assert _coba(rekon=0, cocok=1, bertaut=0).detail["code"] == "BANK_TX_MATCHED"


def test_tanpa_kolom_bertaut_perilaku_lama_utuh_dan_tak_bertaut_lolos():
    assert asyncio.run(J.tolak_void_bila_terekonsiliasi(_Conn(rekon=0, cocok=0), T, uuid.uuid4())) is None
    assert asyncio.run(J.tolak_void_bila_terekonsiliasi(_Conn(rekon=0, cocok=0, bertaut=0), T, uuid.uuid4())) is None


def test_sql_penjaga_menghitung_tautan_aktif_bertenant_dan_tak_membaca_kolom_rekon_lama_untuk_bank_feed():
    sq = " ".join(J.SQL_KEADAAN.split())
    assert "FROM mutasi_bank_tautan t WHERE t.tenant_id = bt.tenant_id AND t.bank_transaction_id = bt.id AND t.dilepas_pada IS NULL" in sq and "AS bertaut" in sq


EXPECTED_PEMANGGIL = {
    "routers/receive_payments.py", "routers/customer_deposits.py", "routers/vendor_deposits.py", "routers/sales_receipts.py", "routers/expenses.py",
    "services/uang_muka_vendor_rencana.py", "services/bayar_keluar_rencana.py", "services/kasbank_tulis.py", "services/uang_muka_rencana.py", "services/biaya_aksi.py"}


def _app():
    return os.path.dirname(os.path.dirname(J.__file__))


def test_semua_pemanggil_penjaga_void_terdaftar_dan_tak_ada_pemanggil_gelap():
    ditemukan = set()
    for akar, _, berkas in os.walk(_app()):
        for f in berkas:
            if f.endswith(".py") and f != "jaga_rekonsiliasi.py":
                path = os.path.join(akar, f)
                for n in ast.walk(ast.parse(open(path).read())):
                    if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) == "tolak_void_bila_terekonsiliasi":
                        ditemukan.add(os.path.relpath(path, _app()))
    assert ditemukan == EXPECTED_PEMANGGIL, f"pemanggil baru/hilang: {sorted(ditemukan ^ EXPECTED_PEMANGGIL)} (pemanggil baru wajib didaftarkan + diuji bertaut)"


def test_bank_feed_tak_pernah_menulis_kolom_rekonsiliasi_lama():
    app = _app()
    for f in os.listdir(os.path.join(app, "services")):
        if f.startswith("bankfeed_") and f.endswith(".py"):
            tree = ast.parse(open(os.path.join(app, "services", f)).read())
            doc = tree.body[0].value if tree.body and isinstance(tree.body[0], ast.Expr) else None
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc:
                    assert "matched_statement_line_id" not in n.value and "UPDATE bank_transactions" not in n.value, (f, n.value[:60])
