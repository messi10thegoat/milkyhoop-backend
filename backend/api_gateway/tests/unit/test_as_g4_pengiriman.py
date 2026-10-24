"""AS-G4 (24 Okt 2026): idem batal Surat Jalan (SJ_VOID) + pratinjau & idem ubah serah-terima (SJ_SERAH). Penentu serah SATU untuk PUT dan
pratinjau; pratinjau READ ONLY. Buat SJ dari pesanan SUDAH idem (SJ_SO) -- tiket lama keliru (diukur per handler)."""
import inspect

from app.routers import deliveries as D
from app.routers import sj_so as SJ


def test_idem_batal_sj_sebelum_void_dan_respons_dicatat():
    s = inspect.getsource(SJ.void_sj)
    assert "idem_buat.kunci_dari(request)" in s and 'idem_buat.mulai_aksi(conn, ctx, kunci, "SJ_VOID"' in s
    assert s.index("idem_buat.mulai_aksi(") < s.index("S.void(")  # ulang ber-kunci = replay TANPA memanggil void lagi (nol pembalik kedua)
    assert '"SJ_VOID", resp, fid) if kp else resp' in s


def test_buat_sj_dari_pesanan_sudah_idem_kontrol_positif():
    s = inspect.getsource(SJ.create_sj_so)
    assert "idem_buat.mulai(" in s and "idem_buat.kunci_dari(request)" in s


def test_serah_penentu_satu_untuk_put_dan_pratinjau():
    put = inspect.getsource(D.ubah_serah_surat_jalan)
    pv = inspect.getsource(D.preview_ubah_serah_surat_jalan)
    assert "_rencana_serah(conn, tid, fid, body)" in put and "_rencana_serah(conn, ctx[\"tenant_id\"], fid, b)" in pv
    assert "transaction(readonly=True)" in pv and "rollback()" in pv and "pg_advisory" not in pv  # pratinjau READ ONLY tanpa kunci
    assert put.index("idem_buat.mulai_aksi(") < put.index("_rencana_serah(") and '"SJ_SERAH"' in put
    r = inspect.getsource(D._rencana_serah)
    assert "pastikan_aktif" in r and '"SJ_VOID"' in r and "selesaikan(conn, tid, sj[\"sales_order_id\"], body, pakai_bawaan=False)" in r
    assert "check_period_is_open" not in r and "fiscal_periods" not in r  # K4: periode tertutup BOLEH (penjaga lama ikut penentu)


def test_rute_dan_izin_nyata():
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert "/{delivery_id}/serah/preview" in [r.path for r in D.router.routes]
    assert izin_untuk_jalur("/api/deliveries/6a263caa-0ea6-47e1-aa78-fc6575e38ff0/serah/preview", "POST") == ("sales_invoice", "U")
    assert izin_untuk_jalur("/api/deliveries/6a263caa-0ea6-47e1-aa78-fc6575e38ff0/serah", "PUT") == ("sales_invoice", "U")
    assert izin_untuk_jalur("/api/deliveries/6a263caa-0ea6-47e1-aa78-fc6575e38ff0/void", "POST") == ("sales_invoice", "V")


def test_ulang_berkunci_tak_memanggil_void_lagi_asal_apa_pun(monkeypatch):
    """Unit (berlaku asal PESANAN maupun FAKTUR -- idem di RUTE sebelum S.void): ulang ber-kunci = replay, S.void dipanggil SEKALI."""
    import asyncio
    from types import SimpleNamespace
    from app.services import idem_buat
    panggil, simpan = [], {}

    class Tr:
        async def __aenter__(s): return s
        async def __aexit__(s, *a): return False

    class Kon:
        async def execute(s, *a): return None
        def transaction(s): return Tr()

    class CM:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def pool(): return P()

    async def void(conn, ctx, fid, alasan):
        panggil.append(fid)
        return {"ulang": False}

    async def mulai_aksi(conn, ctx, kunci, prefix, doc, isi, response=None):
        assert prefix == "SJ_VOID"
        return ("k", "s", simpan.get("r"))

    async def simpan_(conn, ctx, kp, sidik, src, resp, rid):
        simpan["r"] = resp
        return resp
    monkeypatch.setattr(SJ, "_pool", pool)
    monkeypatch.setattr(SJ.S, "void", void)
    monkeypatch.setattr(idem_buat, "kunci_dari", lambda request, kunci_body=None: "kunci-1")
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    monkeypatch.setattr(idem_buat, "simpan", simpan_)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t", "user_id": "u"}))
    fid = "6a263caa-0ea6-47e1-aa78-fc6575e38ff0"
    r1 = asyncio.run(SJ.void_sj(req, fid, SJ.AlasanIn(reason="x")))
    r2 = asyncio.run(SJ.void_sj(req, fid, SJ.AlasanIn(reason="x")))
    assert r1 == r2 and len(panggil) == 1  # kontrol: tanpa replay (simpan kosong) panggilan kedua AKAN memanggil void lagi
    simpan.clear()
    asyncio.run(SJ.void_sj(req, fid, SJ.AlasanIn(reason="x")))
    assert len(panggil) == 2
