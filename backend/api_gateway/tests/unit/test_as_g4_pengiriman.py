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
