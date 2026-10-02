"""Kode order (V359; 2 Okt 2026, pemilik + MASTER; pola SAP number range / NetSuite auto-generated numbers).
Aturan murni: templat, periode reset, urai kode (untuk ganti manual/impor), judul. Penerbitan + paritas jurnal diuji
nyata di salinan DB (harness di luar suite)."""
import inspect
from datetime import date

import pytest

from app.services import kode_order as KO

T = date(2026, 10, 2)


@pytest.mark.parametrize("tpl,digit,seq,harap", [
    ("{SEQ}-{MM}-{YY}", 3, 1, "001-10-26"),       # grapgrap (worksheet pemilik)
    ("{SEQ}-{MM}-{YY}", 3, 123, "123-10-26"),
    ("{SEQ}-{MM}-{YY}", 3, 1234, "1234-10-26"),   # lewat digit minimal: tak terpotong
    ("JO/{YYYY}/{SEQ}", 4, 7, "JO/2026/0007"),
    ("{SEQ}", 1, 5, "5"),
])
def test_render(tpl, digit, seq, harap):
    assert KO.render(tpl, seq, digit, T) == harap


@pytest.mark.parametrize("reset,harap", [("never", "ALL"), ("yearly", "2026"), ("monthly", "2026-10")])
def test_kunci_periode(reset, harap):
    assert KO.kunci_periode(reset, T) == harap


@pytest.mark.parametrize("tpl,reset", [
    ("{SEQ}{SEQ}", "never"), ("ABC", "never"), ("{SEQ}-{DD}", "never"),
    ("{SEQ}-{YY}", "monthly"),           # bulanan tanpa {MM} -> bentrok antarbulan
    ("{SEQ}", "yearly"),                 # tahunan tanpa tahun
])
def test_setelan_ditolak(tpl, reset):
    with pytest.raises(KO.KodeOrderGalat):
        KO.validasi_setelan(tpl, 3, reset, "so_confirmed")


def test_setelan_sah():
    KO.validasi_setelan("{SEQ}-{MM}-{YY}", 3, "monthly", "so_confirmed")
    KO.validasi_setelan("JO/{YYYY}/{SEQ}", 4, "yearly", "manual_only")


def test_urai_dan_periode():
    u = KO.urai("{SEQ}-{MM}-{YY}", "045-09-26")
    assert u == {"SEQ": 45, "MM": 9, "YY": 26} and KO.periode_dari_urai("monthly", u) == "2026-09"
    assert KO.urai("{SEQ}-{MM}-{YY}", "045-13-26") is None       # bulan mustahil
    assert KO.urai("{SEQ}-{MM}-{YY}", "KEMEJA-01") is None
    assert KO.periode_dari_urai("yearly", KO.urai("JO/{YYYY}/{SEQ}", "JO/2026/0012")) == "2026"


def test_judul_dan_kode():
    assert KO.normal_judul("  kemeja   gmim ") == "KEMEJA GMIM"
    assert KO.normal_judul("") is None and KO.normal_judul(None) is None
    with pytest.raises(KO.KodeOrderGalat):
        KO.normal_judul("x" * 61)
    with pytest.raises(KO.KodeOrderGalat):
        KO.normal_kode("   ")                      # mengosongkan = bolong -> DILARANG (putusan MASTER)
    assert KO.normal_kode(" 001-10-26 ") == "001-10-26"


def test_penerbitan_kunci_per_so_lalu_periode_dan_tanpa_jurnal():
    src = inspect.getsource(KO.terbitkan)
    assert src.index("ORDER_CODE_SO:") < src.index('f"ORDER_CODE:{tenant_id}:{pk}"')
    assert "order_code IS NULL" in src and "journal_" not in src


def test_patch_judul_jalur_sendiri_sebelum_penjaga_draf():
    from app.routers import sales_orders as SO
    src = inspect.getsource(SO.update_sales_order)
    assert src.index('"order_title" in body.model_fields_set') < src.index('Only draft orders can be updated')


def test_detail_selalu_membawa_kunci_kode_dan_judul():
    from app.schemas.sales_orders import SalesOrderDetail
    f = SalesOrderDetail.model_fields
    assert {"order_code", "order_title", "order_code_source", "order_code_can_override"} <= set(f)
    assert f["order_code_can_override"].default is False


def test_patch_hanya_judul_lolos_di_so_confirmed(monkeypatch):
    """PERILAKU (bukan teks sumber): PATCH {order_title} pada SO confirmed -> 200 + ubah_judul dipanggil, tak ada
    UPDATE field lain. Sabotase 'if False and ...' memerahkan ini (pemeriksaan teks dulu tetap hijau)."""
    import asyncio
    import uuid as _u
    from app.routers import sales_orders as SO
    from app.schemas.sales_orders import UpdateSalesOrderRequest
    from app.services import optimistic_concurrency as OC
    sid = _u.uuid4()
    dipanggil, tulis = [], []

    class _C:
        def transaction(self):
            class _T:
                async def __aenter__(s): return None
                async def __aexit__(s, *a): return False
            return _T()

        async def fetchrow(self, sql, *a):
            return {"id": sid, "status": "confirmed"}

        async def execute(self, sql, *a):
            tulis.append(sql)

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s): return _C()
                async def __aexit__(s, *a): return False
            return _A()

    async def gp(): return _P()
    async def lolos(*a, **k): return None
    async def judul(conn, tid, so_id, j, aktor):
        dipanggil.append(j)
        return {"status": 200}
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": None})
    monkeypatch.setattr(OC, "assert_if_match_row", lolos)
    monkeypatch.setattr(KO, "ubah_judul", judul)
    r = asyncio.run(SO.update_sales_order(object(), str(sid), UpdateSalesOrderRequest(order_title="kemeja")))
    assert r.success and dipanggil == ["kemeja"] and not any("UPDATE sales_orders SET" in s for s in tulis)


def test_inti_uang_masuk_TIDAK_menerbitkan_kode():
    """Putusan pemilik LANGSUNG 2 Okt (murni SAP/NetSuite): kode terbit saat SO DIKONFIRMASI saja. Inti DP /
    penerapan / penerimaan dikembalikan AST-identik ke sebelum kait (jurnal identik dgn sendirinya)."""
    from app.routers import customer_deposits as CD, receive_payments as RP
    for fn in (CD._post_deposit, CD.apply_deposit_core, RP._post_payment):
        assert "terbitkan" not in inspect.getsource(fn) and "kode_order" not in inspect.getsource(fn)


def test_konfirmasi_so_menerbitkan_kode_dalam_transaksi():
    from app.routers import sales_orders as SO
    src = inspect.getsource(SO.confirm_sales_order)
    assert "async with pool.acquire() as conn, conn.transaction():" in src
    i = src.index("_terbitkan_kode(")
    assert src.index("UPDATE sales_orders SET status = 'confirmed'") < i and '"so_confirmed"' in src[i:i + 200]


def test_pemicu_hanya_so_confirmed_dan_manual():
    assert KO.PEMICU == ("so_confirmed", "manual_only") and KO.BAWAAN["trigger"] == "so_confirmed"
    assert KO.BAWAAN["min_digits"] == 4 and KO.BAWAAN["label"] == "Kode order"
    with pytest.raises(KO.KodeOrderGalat):
        KO.validasi_setelan("{SEQ}", 4, "never", "first_payment")


def test_label_cetak_memotong_judul():
    assert KO.potong_judul("KEMEJA GMIM") == "KEMEJA GMIM"
    p = KO.potong_judul("X" * 60)
    assert len(p) == KO.MAKS_JUDUL_CETAK and p.endswith("…")


def test_boleh_isi_kode_tak_bergantung_kode_sudah_ada():
    """manual_only / SO tanpa kode: pensil HARUS bisa tampil untuk MENGISI kode (2 Okt, temuan uji nyata FRONTEND)."""
    from app.routers import sales_orders as SO
    src = inspect.getsource(SO.get_sales_order_detail)
    i = src.index("_boleh_kode = ")
    assert "order_code" not in src[i:src.index("\n", i)]


def test_impor_galat_atau_pratinjau_tak_menulis_dan_urutan_kunci():
    """Impor (diuji nyata di salinan DB 8/8: dry_run nol tulis, galat -> 422 nol tulis, ambigu ditolak, penghitung
    melompat, konfirmasi berikutnya melanjutkan sesudah impor, bukan pemilik 403). Di sini: galat ATAU dry_run kembali
    SEBELUM tulisan pertama; kunci per-SO diambil sebelum kunci periode (urutan sama dengan terbitkan)."""
    src = inspect.getsource(KO.impor)
    kembali = src.index("if dry_run or galat_n:")
    assert kembali < src.index("UPDATE sales_orders") and kembali < src.index("INSERT INTO order_code_counters")
    assert src.index('f"ORDER_CODE_SO:{tenant_id}:{so_id}"') < src.index('f"ORDER_CODE:{tenant_id}:{pk}"')
