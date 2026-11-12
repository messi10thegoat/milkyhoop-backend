"""BF-1d: aksi CATAT jenis bayar_tagihan (uang keluar -> pembayaran faktur pembelian) + BATALKAN BILL_PAYMENT. Jurnal HANYA lewat penulis modul (buat_bayar_core, void_bayar_core).
Putusan pemilik 12 Nov: TANPA PPh (tagihan ber-PPh ditolak), diskon+biaya bank NOL, kelebihan ikut modul + tampil; MASTER: alokasi eksplisit, sen boleh. TES MERAH DULU (dijalankan sesudah jendela malam)."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.routers import bill_payments as BP
from app.services import bankfeed_aksi as BA
from app.services import kasbank_tulis as KT
from app.services.bayar_keluar_rencana import Blok
from app.services import bayar_keluar_rencana as BKR
from .test_bankfeed_catat3 import AKUN_DP, BT, CTX, MID, REK, T, USER, _K as _K3, _dok, _kode, _m, _tt  # noqa: F401

VENDOR, BILL, BILL2, PAY, COA = (uuid.uuid4() for _ in range(5))


class _K(_K3):
    """Fake c3 + vendor + jurnal pembayaran."""
    async def fetchrow(self, sql, *a):
        if sql.startswith("SELECT id, name FROM vendors"):
            return {"id": VENDOR, "name": "PT Vendor"} if not getattr(self, "vendor_hilang", False) else None
        if sql.startswith("SELECT journal_id FROM bill_payments_v2"):
            self.sql.append((sql, a))
            return {"journal_id": uuid.UUID(int=9)}
        return await super().fetchrow(sql, *a)


@pytest.fixture(autouse=True)
def _stub_dasar(monkeypatch):
    async def nama(conn, ids, cadangan_email=True):
        return {str(i): "Sinta" for i in ids}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")

    async def akun(conn, tid, ba_id, per_tanggal=None):
        return {"id": REK, "account_name": "BCA UJI", "account_type": getattr(akun, "tipe", "bank"), "coa_id": COA, "is_active": True, "saldo": D("9000000")}
    akun.tipe = "bank"
    monkeypatch.setattr(BA, "nama_untuk", nama)
    monkeypatch.setattr(BA, "zona_tenant", zona)
    monkeypatch.setattr(KT, "akun", akun)
    return akun


ALOK = [{"bill_id": str(BILL), "nominal": "500000"}]


def _stub_bayar(monkeypatch, pph=D(0), unapplied=D(0), blocks=None, bank_amount=None):
    rekam = {"rencana": [], "inti": []}

    async def rencana(conn, ctx, payload, pratinjau=False):
        conn.urut.append("rencana_bayar")
        rekam["rencana"].append((payload, pratinjau))
        total = D(payload.total_amount)
        return {"blocks": list(blocks or []), "vendor_name": "PT Vendor", "bank_account_name": "BCA UJI", "allocated_amount": total - unapplied, "unapplied_amount": unapplied,
                "pph": {"tax_code_id": None, "amount": pph, "base": D(0)},
                "journals": [{"lines": [{"account_code": "2-10100", "account_name": "Hutang Usaha", "debit": float(total), "credit": 0.0}, {"account_code": "1-10201", "account_name": "BCA", "debit": 0.0, "credit": float(total)}]}],
                "bank_transactions": [{"bank_account": "BCA UJI", "amount": float(-(total - pph) if bank_amount is None else bank_amount), "transaction_type": "payment_made"}],
                "bills": [{"bill_id": str(BILL), "bill_number": "BILL-1", "remaining_after": 0.0}]}

    async def inti(conn, ctx, payload):
        conn.urut.append("buat_bayar_core")
        rekam["inti"].append(payload)
        return {"success": True, "message": "ok", "data": {"id": str(PAY), "payment_number": "BP-2611-0001", "status": "posted"}}
    monkeypatch.setattr(BKR, "rencana_bayar", rencana)
    monkeypatch.setattr(BP, "buat_bayar_core", inti)
    return rekam


def _prev(k, body, ctx=None):
    return asyncio.run(BA.RENCANA["catat"](k, T, REK, MID, body, Blok(True), False, ctx))


def _tulis(k, body, ctx=None):
    return asyncio.run(BA.tulis(k, ctx or CTX, REK, MID, "catat", body))


def _keluar(**k):
    return _m(arah="keluar", keterangan="TRSF E-BANKING DB 1010/FTSCY/WS95031 PT VENDOR", **k)


# ====================================================================== validasi
def test_bayar_tagihan_hanya_uang_keluar_kontak_wajib_vendor_harus_ada(monkeypatch):
    _stub_bayar(monkeypatch)
    assert _kode(_prev(_K(_m(arah="masuk")), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})) == ["JENIS_TAK_SESUAI_ARAH"]
    assert _kode(_prev(_K(_keluar()), {"jenis": "bayar_tagihan", "alokasi": ALOK})) == ["KONTAK_WAJIB"]
    k = _K(_keluar())
    k.vendor_hilang = True
    assert _kode(_prev(k, {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})) == ["VENDOR_TAK_ADA"]


def test_bayar_tagihan_alokasi_wajib_sah_tanpa_ganda_dan_tak_melebihi_nominal(monkeypatch):
    _stub_bayar(monkeypatch)
    b = {"jenis": "bayar_tagihan", "kontak": str(VENDOR)}
    assert _kode(_prev(_K(_keluar()), b)) == ["ALOKASI_WAJIB"]
    assert _kode(_prev(_K(_keluar()), {**b, "alokasi": [{"bill_id": "bukan-uuid", "nominal": "1"}]})) == ["ALOKASI_TAK_SAH"]
    assert _kode(_prev(_K(_keluar()), {**b, "alokasi": [{"bill_id": str(BILL), "nominal": "0"}]})) == ["ALOKASI_TAK_SAH"]
    assert _kode(_prev(_K(_keluar()), {**b, "alokasi": [{"bill_id": str(BILL), "nominal": "100"}, {"bill_id": str(BILL), "nominal": "100"}]})) == ["ALOKASI_TAK_SAH"]
    assert _kode(_prev(_K(_keluar()), {**b, "alokasi": [{"bill_id": str(BILL), "nominal": "600000"}]})) == ["ALOKASI_MELEBIHI_NOMINAL"]


def test_bayar_tagihan_nominal_bersen_diterima(monkeypatch):
    _stub_bayar(monkeypatch)
    h = _prev(_K(_keluar(nominal=D("500000.50"))), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": [{"bill_id": str(BILL), "nominal": "500000.50"}]})
    assert "NOMINAL_BERSEN" not in _kode(h) and h["blocks"] == []


# ====================================================================== isian penulis
def test_bayar_tagihan_isian_ke_penulis_id_rekening_diskon_biaya_nol_kunci_berurut(monkeypatch):
    rekam = _stub_bayar(monkeypatch)
    _prev(_K(_keluar(), jumlah_tautan=1), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK, "catatan": "lunas BILL-1"})
    p, pratinjau = rekam["rencana"][-1]
    assert (p.vendor_id, p.vendor_name, p.payment_date, p.payment_method, p.bank_account_id, p.bank_account_name, p.total_amount) == \
        (str(VENDOR), "PT Vendor", date(2026, 10, 10), "bank_transfer", str(REK), "BCA UJI", D("500000.00")) and pratinjau is True     # bank_accounts.id, BUKAN CoA
    assert (p.discount_amount, p.bank_fee_amount, p.save_as_draft, p.notes) == (0, 0, False, "lunas BILL-1") and p.idempotency_key == f"BANKFEED:{MID}:2"
    assert [(a.bill_id, a.amount_applied) for a in p.allocations] == [(str(BILL), D("500000"))]


def test_bayar_tagihan_rekening_kas_memakai_metode_cash(monkeypatch, _stub_dasar):
    rekam = _stub_bayar(monkeypatch)
    _stub_dasar.tipe = "cash"
    _prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})
    assert rekam["rencana"][-1][0].payment_method == "cash"


# ====================================================================== putusan pemilik: tanpa PPh, kelebihan tampil
def test_tagihan_ber_pph_ditolak_dengan_arahan_ke_form_biasa_dan_kontrol_tanpa_pph_lolos(monkeypatch):
    _stub_bayar(monkeypatch, pph=D("10000"))
    h = _prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})
    assert _kode(h) == ["TAGIHAN_BER_PPH"] and "Bayar Tagihan" in h["blocks"][0]["message"]
    _stub_bayar(monkeypatch, pph=D(0))
    assert _prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})["blocks"] == []


def test_kelebihan_ikut_modul_dan_ditampilkan(monkeypatch):
    _stub_bayar(monkeypatch, unapplied=D("100000"))
    h = _prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": [{"bill_id": str(BILL), "nominal": "400000"}]})
    assert h["blocks"] == [] and h["kelebihan"]["jumlah"] == "100000.00" and "kelebihan" in h["kalimat"].lower() and "Rp 100.000" in h["kalimat"]
    _stub_bayar(monkeypatch)
    h = _prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})
    assert h["kelebihan"] is None and "kelebihan" not in h["kalimat"].lower()


def test_nominal_bank_menurut_rencana_diverifikasi_ulang(monkeypatch):
    _stub_bayar(monkeypatch, bank_amount=-499999.0)
    assert "NOMINAL_JURNAL_TAK_SAMA" in _kode(_prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK}))


def test_blok_penentu_modul_diteruskan(monkeypatch):
    _stub_bayar(monkeypatch, blocks=[{"code": "TAGIHAN_VENDOR_LAIN", "message": "milik vendor lain"}])
    assert "TAGIHAN_VENDOR_LAIN" in _kode(_prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK}))


# ====================================================================== tulis
def test_tulis_urutan_kunci_domain_penulis_cari_bt_dari_jurnal_pembayaran_lalu_tautan(monkeypatch):
    rekam = _stub_bayar(monkeypatch)
    body = {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK}
    body["versi"] = _prev(_K(_keluar()), body)["versi"]
    k = _K(_keluar(), bt_jurnal=(BT,))
    r = _tulis(k, body)
    u = k.urut
    assert u.index("kunci:MUTASI_BANK") < u.index("set_tenant") < u.index("baca_mutasi") < u.index("kunci:BILL_PAYMENT_CREATE") < u.index("rencana_bayar") < u.index("buat_bayar_core") < u.index("tautan") < u.index("putus") < u.index("riwayat")
    kunci = next(a for s, a in k.sql if "pg_advisory_xact_lock" in s and str(a[0]).startswith("BILL_PAYMENT_CREATE"))
    assert kunci[0] == f"BILL_PAYMENT_CREATE:{T}:{VENDOR}"                       # kunci domain SAMA dengan rute (per tenant + vendor)
    sql_jurnal = next(s for s, _ in k.sql if s.startswith("SELECT journal_id FROM bill_payments_v2"))
    assert "tenant_id" in sql_jurnal
    t_arg = next(a for s, a in k.sql if s.startswith("INSERT INTO mutasi_bank_tautan"))
    assert t_arg[1:4] == (MID, BT, D("500000.00")) and "BP-2611-0001" in r["kalimat"] and len(rekam["inti"]) == 1


def test_tulis_bt_hasil_tak_ditemukan_atau_ganda_membatalkan_semua(monkeypatch):
    _stub_bayar(monkeypatch)
    for bts in ((), (BT, uuid.uuid4())):
        k = _K(_keluar(), bt_jurnal=bts)
        with pytest.raises(HTTPException) as e:
            _tulis(k, {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK})
        assert e.value.status_code == 409 and e.value.detail["code"] == "BT_HASIL_TAK_DITEMUKAN" and "putus" not in k.urut


# ====================================================================== konstanta + izin
def test_semua_jenis_tersedia_dan_izin_modul_tagihan_terpasang():
    assert BA.JENIS_BELUM == () and "bayar_tagihan" in BA.JENIS_CATAT and "bayar_tagihan" in BA.JENIS_DOK and "bayar_tagihan" in BA.JENIS_GUC
    assert BA.IZIN_BUAT["bayar_tagihan"] == "/api/bill-payments" and BA.IZIN_VOID["BILL_PAYMENT"] == "/api/bill-payments/{}/void" and "BILL_PAYMENT" in BA.TIPE_DOK


def test_catat_butuh_izin_buat_modul_tagihan(monkeypatch):
    _stub_bayar(monkeypatch)
    diminta = []

    async def izin(user, jalur, method="GET"):
        diminta.append((tuple(jalur), method))
        return {j: False for j in jalur}
    monkeypatch.setattr(BA, "izin_rute_pengguna", izin, raising=False)
    h = _prev(_K(_keluar()), {"jenis": "bayar_tagihan", "kontak": str(VENDOR), "alokasi": ALOK}, {**CTX, "user": {"user_id": USER, "tenant_id": T}})
    assert _kode(h)[:1] == ["IZIN_DITOLAK"] and (("/api/bill-payments",), "POST") in diminta


# ====================================================================== batalkan BILL_PAYMENT
def _stub_batal(monkeypatch, blocks=None):
    rekam = []

    async def r_batal(conn, tid, pay_id, pratinjau=False):
        conn.urut.append("rencana_batal_bayar")
        rekam.append(("rencana_batal_bayar", str(pay_id), pratinjau))
        return {"blocks": list(blocks or []), "payment": {"payment_number": "BP-2611-0001"}, "ulang": False}

    async def kunci(conn, pay_id):
        conn.urut.append("kunci:BILL_PAYMENT_VOID")

    async def inti(conn, ctx, pay_id, alasan, r):
        conn.urut.append("void_bayar_core")
        rekam.append(("void_bayar_core", str(pay_id), alasan))
        return {"void_journal_id": uuid.uuid4()}
    monkeypatch.setattr(BKR, "rencana_batal_bayar", r_batal)
    monkeypatch.setattr(BP, "kunci_void_bayar", kunci)
    monkeypatch.setattr(BP, "void_bayar_core", inti)
    return rekam


def test_batalkan_bill_payment_urutan_dan_dispatch(monkeypatch):
    rekam = _stub_batal(monkeypatch)
    dok = _dok("BILL_PAYMENT", None, PAY)
    body = {"alasan": "salah bayar"}
    body["versi"] = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=dok), T, REK, MID, body, Blok(True), False, None))["versi"]
    k = _K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=dok)
    asyncio.run(BA.tulis(k, CTX, REK, MID, "batalkan", body))
    u = k.urut
    assert u.index("lepas_tautan") < u.index("kunci:BILL_PAYMENT_VOID") < u.index("rencana_batal_bayar") < u.index("void_bayar_core") < u.index("kembali") < u.index("riwayat")
    assert ("void_bayar_core", str(PAY), "salah bayar") in rekam


def test_batalkan_bill_payment_blok_void_pada_tulis_dilempar_tanpa_menulis(monkeypatch):
    _stub_batal(monkeypatch, blocks=[{"code": "PERIODE_TERTUTUP", "message": "ditutup"}])
    k = _K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok("BILL_PAYMENT", None, PAY))
    with pytest.raises(HTTPException) as e:
        asyncio.run(BA.tulis(k, CTX, REK, MID, "batalkan", {"alasan": "salah bayar"}))
    assert "void_bayar_core" not in k.urut and "kembali" not in k.urut and e.value.status_code in (400, 403, 409)


def test_batalkan_pratinjau_bill_payment_meneruskan_blok_dan_menyaring_penjaga_tautan(monkeypatch):
    _stub_batal(monkeypatch, blocks=[{"code": "BANK_TX_BERTAUT", "message": "x"}, {"code": "PERIODE_TERTUTUP", "message": "ditutup"}])
    h = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok("BILL_PAYMENT", None, PAY)), T, REK, MID, {"alasan": "salah"}, Blok(True), False, None))
    assert _kode(h) == ["PERIODE_TERTUTUP"]


def test_batalkan_butuh_izin_void_modul_tagihan(monkeypatch):
    _stub_batal(monkeypatch)
    diminta = []

    async def izin(user, jalur, method="GET"):
        diminta.append((tuple(jalur), method))
        return {j: False for j in jalur}
    monkeypatch.setattr(BA, "izin_rute_pengguna", izin, raising=False)
    h = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok("BILL_PAYMENT", None, PAY)), T, REK, MID, {"alasan": "salah"}, Blok(True), False,
                                           {**CTX, "user": {"user_id": USER, "tenant_id": T}}))
    assert "IZIN_DITOLAK" in _kode(h) and ((f"/api/bill-payments/{PAY}/void",), "POST") in diminta
