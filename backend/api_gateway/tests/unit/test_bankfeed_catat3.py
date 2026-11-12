"""BF-1c3: aksi CATAT untuk transfer · uang_muka_pelanggan · terima_pembayaran + BATALKAN generik (dispatch menurut jenis dokumen) + izin modul tujuan. Jurnal HANYA lewat penulis yang ada
(buat_transfer_core, buat_dp_core, buat_penerimaan; void: void_transfer_core, void_deposit_core, _tulis_void_pembayaran). TES MERAH DULU."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.routers import bank_transfers as BTR
from app.routers import customer_deposits as CD
from app.routers import receive_payments as RP
from app.services import bankfeed_aksi as BA
from app.services import kasbank_tulis as KT
from app.services.bayar_keluar_rencana import Blok

T = "t-uji"
REK, LAWAN, MID, BT, BT_LAWAN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
COA_BANK, COA_LAWAN, AKUN_DP, PEL, FAKTUR, TRF, DEP, PAY = (uuid.uuid4() for _ in range(8))
USER = str(uuid.uuid4())
CTX = {"tenant_id": T, "user_id": USER}


def _m(**k):
    d = {"id": MID, "bank_account_id": REK, "tanggal": date(2026, 10, 10), "keterangan": "TRSF E-BANKING CR 1010/FTSCY/WS95031 PT SUMBER", "arah": "masuk", "nominal": D("500000.00"),
         "status": "untuk_ditinjau", "cara": None, "alasan": None, "diputus_oleh": None, "diputus_pada": None, "duplikat_dari": None, "rekonsiliasi_id": None}
    d.update(k)
    return d


class _K:
    def __init__(self, mutasi=None, jumlah_tautan=0, bt_jurnal=(BT,), tautan=None, dok=None, putus=True, kembali=True):
        self.mutasi, self.jumlah_tautan, self.bt_jurnal, self.tautan, self.dok, self.putus, self.kembali = mutasi, jumlah_tautan, list(bt_jurnal), tautan, dok, putus, kembali
        self.sql, self.urut = [], []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if sql.startswith("SELECT id, bank_account_id, tanggal"):
            self.urut.append("baca_mutasi")
            return self.mutasi
        if sql.startswith("SELECT id, nama FROM customers"):
            return {"id": PEL, "nama": "PT Sumber Rejeki"}
        if sql.startswith("SELECT bt.id AS bt_id, je.source_type"):
            return self.dok
        if sql.startswith("UPDATE mutasi_bank SET status = $4"):
            self.urut.append("putus")
            return {"id": MID} if self.putus else None
        if sql.startswith("UPDATE mutasi_bank SET status = $3"):
            self.urut.append("kembali")
            return {"id": MID} if self.kembali else None
        raise AssertionError("fetchrow SQL tak dikenal: " + sql[:80])

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM mutasi_bank_tautan WHERE tenant_id = $1 AND mutasi_bank_id = $2" in sql:
            return self.jumlah_tautan
        if "reversal_of_id" in sql:
            return "RV-UJI"
        raise AssertionError("fetchval SQL tak dikenal: " + sql[:80])

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if sql.startswith("SELECT id FROM bank_transactions"):
            return [{"id": x} for x in self.bt_jurnal]
        if "FROM mutasi_bank_tautan t JOIN bank_transactions" in sql:
            return self.tautan if self.tautan is not None else []
        if "FROM chart_of_accounts" in sql:
            return []
        if "FROM journal_lines" in sql:
            return []
        raise AssertionError("fetch SQL tak dikenal: " + sql[:80])

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        if "pg_advisory_xact_lock" in sql:
            self.urut.append("kunci:" + str(a[0]).split(":")[0])
        elif "set_config('app.tenant_id'" in sql:
            self.urut.append("set_tenant")
        elif sql.startswith("INSERT INTO mutasi_bank_tautan"):
            self.urut.append("tautan")
        elif sql.startswith("UPDATE mutasi_bank_tautan SET dilepas_oleh"):
            self.urut.append("lepas_tautan")
        elif sql.startswith("INSERT INTO audit_logs"):
            self.urut.append("riwayat")


@pytest.fixture(autouse=True)
def _stub_dasar(monkeypatch):
    async def nama(conn, ids, cadangan_email=True):
        return {str(i): "Sinta" for i in ids}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")

    async def akun(conn, tid, ba_id, per_tanggal=None):
        return {"id": REK, "account_name": "BCA UJI", "account_type": "bank", "coa_id": COA_BANK, "is_active": True, "saldo": D("2000000")}

    async def peran(conn, tid, peran_):
        return AKUN_DP
    monkeypatch.setattr(BA, "nama_untuk", nama)
    monkeypatch.setattr(BA, "zona_tenant", zona)
    monkeypatch.setattr(KT, "akun", akun)
    monkeypatch.setattr(BA, "resolve_account_id_by_role", peran, raising=False)


def _prev(k, body, ctx=None):
    return asyncio.run(BA.RENCANA["catat"](k, T, REK, MID, body, Blok(True), False, ctx))


def _kode(h):
    return [b["code"] for b in h["blocks"]]


def _tulis(k, aksi, body, ctx=None):
    return asyncio.run(BA.tulis(k, ctx or CTX, REK, MID, aksi, body))


def _kodeg(e):
    return e.code if hasattr(e, "code") else e.detail["code"]


def _galat(k, aksi, body, ctx=None):
    with pytest.raises(HTTPException) as e:
        _tulis(k, aksi, body, ctx)
    return e.value


# ====================================================================== TRANSFER
def _stub_transfer(monkeypatch, blocks=None, selisih=D(0)):
    rekam = {"rencana": [], "inti": []}

    async def rencana(conn, tid, body, pratinjau):
        conn.urut.append("rencana_buat_transfer")
        rekam["rencana"].append((body, pratinjau))
        n = D(body.amount)
        asal = {"id": UUID_(body.from_bank_id), "coa_id": COA_BANK if UUID_(body.from_bank_id) == REK else COA_LAWAN, "account_name": "A"}
        tujuan = {"id": UUID_(body.to_bank_id), "coa_id": COA_BANK if UUID_(body.to_bank_id) == REK else COA_LAWAN, "account_name": "B"}
        return {"blocks": list(blocks or []), "asal": asal, "tujuan": tujuan, "baris": [(tujuan["coa_id"], n + selisih, D(0), "t"), (asal["coa_id"], D(0), n, "t")], "saldo": []}

    async def inti(conn, ctx, body):
        conn.urut.append("buat_transfer_core")
        rekam["inti"].append(body)
        return {"transfer_id": TRF, "transfer_number": "TRF-2611-0001",
                "post": {"journal_id": "j-trf", "journal_number": "JT-1", "from_transaction_id": str(BT), "to_transaction_id": str(BT_LAWAN)}}

    async def kunci(conn, ba_id):
        pass
    monkeypatch.setattr(KT, "rencana_buat_transfer", rencana)
    monkeypatch.setattr(BTR, "buat_transfer_core", inti)
    return rekam


def UUID_(x):
    return x if isinstance(x, uuid.UUID) else uuid.UUID(str(x))


def test_transfer_lawan_wajib_dan_tak_boleh_rekening_sendiri(monkeypatch):
    _stub_transfer(monkeypatch)
    assert _kode(_prev(_K(_m()), {"jenis": "transfer"})) == ["TRANSFER_LAWAN_WAJIB"]
    assert _kode(_prev(_K(_m()), {"jenis": "transfer", "rekening_lawan": str(REK)})) == ["TRANSFER_LAWAN_SAMA"]


def test_transfer_arah_keluar_dari_rekening_ini_masuk_ke_rekening_ini(monkeypatch):
    rekam = _stub_transfer(monkeypatch)
    _prev(_K(_m(arah="keluar")), {"jenis": "transfer", "rekening_lawan": str(LAWAN), "catatan": "pindah dana"})
    b, pratinjau = rekam["rencana"][-1]
    assert (b.from_bank_id, b.to_bank_id, b.amount, b.fee_amount, b.transfer_date, b.notes, b.auto_post) == (str(REK), str(LAWAN), 500000, 0, date(2026, 10, 10), "pindah dana", True) and pratinjau is True
    _prev(_K(_m(arah="masuk")), {"jenis": "transfer", "rekening_lawan": str(LAWAN)})
    b, _ = rekam["rencana"][-1]
    assert (b.from_bank_id, b.to_bank_id) == (str(LAWAN), str(REK))


def test_transfer_jurnal_pratinjau_dan_verifikasi_nominal_sisi_rekening(monkeypatch):
    _stub_transfer(monkeypatch)
    h = _prev(_K(_m(arah="keluar")), {"jenis": "transfer", "rekening_lawan": str(LAWAN)})
    assert h["blocks"] == [] and len(h["baris"]) == 2 and h["jenis"] == "transfer" and "Transfer" in h["kalimat"]
    _stub_transfer(monkeypatch, selisih=D("1"))
    assert "NOMINAL_JURNAL_TAK_SAMA" in _kode(_prev(_K(_m(arah="keluar")), {"jenis": "transfer", "rekening_lawan": str(LAWAN)}))


def test_transfer_blok_penentu_diteruskan(monkeypatch):
    _stub_transfer(monkeypatch, blocks=[{"code": "KAS_MINUS", "message": "tak cukup", "status": 400}])
    assert "KAS_MINUS" in _kode(_prev(_K(_m(arah="keluar")), {"jenis": "transfer", "rekening_lawan": str(LAWAN)}))


@pytest.mark.parametrize("arah,bt_sisi_ini", [("keluar", BT), ("masuk", BT_LAWAN)])
def test_transfer_tulis_urutan_tautan_hanya_sisi_rekening_ini(monkeypatch, arah, bt_sisi_ini):
    rekam = _stub_transfer(monkeypatch)
    # keluar: sisi rekening ini = bank asal (from_transaction_id=BT); masuk: sisi ini = bank tujuan (to_transaction_id=BT_LAWAN)
    k = _K(_m(arah=arah))
    body = {"jenis": "transfer", "rekening_lawan": str(LAWAN)}
    body["versi"] = _prev(_K(_m(arah=arah)), body)["versi"]
    r = _tulis(k, "catat", body)
    u = k.urut
    assert u.index("kunci:MUTASI_BANK") < u.index("baca_mutasi") < u.index("rencana_buat_transfer") < u.index("buat_transfer_core") < u.index("tautan") < u.index("putus") < u.index("riwayat")
    t_arg = next(a for s, a in k.sql if s.startswith("INSERT INTO mutasi_bank_tautan"))
    assert t_arg[1:4] == (MID, bt_sisi_ini, D("500000.00")) and len([1 for s, _ in k.sql if s.startswith("INSERT INTO mutasi_bank_tautan")]) == 1     # sisi LAWAN tak ditautkan
    p_arg = next(a for s, a in k.sql if s.startswith("UPDATE mutasi_bank SET status = $4"))
    assert p_arg[3:5] == ("dicatat", "catat") and r["id"] == str(MID) and "TRF-2611-0001" in r["kalimat"] and len(rekam["inti"]) == 1


# ====================================================================== UANG MUKA PELANGGAN
def _stub_dp(monkeypatch, blocks=None):
    rekam = {"rencana": [], "inti": []}

    async def rencana(conn, ctx, body, pratinjau=False):
        conn.urut.append("rencana_buat_dp")
        rekam["rencana"].append((body, pratinjau))
        return {"blocks": list(blocks or []), "order_id": None, "pelanggan_dp": PEL, "jenis": None}

    async def inti(conn, ctx, body, rb):
        conn.urut.append("buat_dp_core")
        rekam["inti"].append(body)
        return {"success": True, "data": {"id": str(DEP), "deposit_number": "DEP-2611-0001", "journal_id": "00000000-0000-0000-0000-0000000000aa", "status": "posted"}}
    monkeypatch.setattr(CD, "rencana_buat_dp", rencana)
    monkeypatch.setattr(CD, "buat_dp_core", inti)
    return rekam


def test_dp_hanya_uang_masuk_kontak_wajib_pelanggan_harus_ada(monkeypatch):
    _stub_dp(monkeypatch)
    assert _kode(_prev(_K(_m(arah="keluar")), {"jenis": "uang_muka_pelanggan", "kontak": str(PEL)})) == ["JENIS_TAK_SESUAI_ARAH"]
    assert _kode(_prev(_K(_m()), {"jenis": "uang_muka_pelanggan"})) == ["KONTAK_WAJIB"]


def test_dp_pelanggan_tak_ada_blok(monkeypatch):
    _stub_dp(monkeypatch)

    class _KP(_K):
        async def fetchrow(self, sql, *a):
            if sql.startswith("SELECT id, nama FROM customers"):
                return None
            return await super().fetchrow(sql, *a)
    assert _kode(_prev(_KP(_m()), {"jenis": "uang_muka_pelanggan", "kontak": str(PEL)})) == ["PELANGGAN_TAK_ADA"]


def test_dp_isian_ke_penulis_rekening_kas_coa_so_dan_kunci_idempotensi_berurutan(monkeypatch):
    rekam = _stub_dp(monkeypatch)
    so = uuid.uuid4()
    _prev(_K(_m(), jumlah_tautan=2), {"jenis": "uang_muka_pelanggan", "kontak": str(PEL), "so_id": str(so), "catatan": "DP pesanan"})
    b, pratinjau = rekam["rencana"][-1]
    assert (b.customer_id, b.customer_name, b.amount, b.deposit_date, b.payment_method, b.account_id, b.bank_account_id, b.sales_order_id, b.notes, b.auto_post) == \
        (str(PEL), "PT Sumber Rejeki", 500000, date(2026, 10, 10), "transfer", str(COA_BANK), str(REK), str(so), "DP pesanan", True) and pratinjau is True
    assert b.idempotency_key == f"BANKFEED:{MID}:3"        # n = tautan lama (2) + 1: tak bentrok saat dicatat ulang setelah batal


def test_dp_rekening_kas_memakai_metode_cash(monkeypatch):
    rekam = _stub_dp(monkeypatch)

    async def akun_kas(conn, tid, ba_id, per_tanggal=None):
        return {"id": REK, "account_name": "KAS", "account_type": "cash", "coa_id": COA_BANK, "is_active": True, "saldo": D(0)}
    monkeypatch.setattr(KT, "akun", akun_kas)
    _prev(_K(_m()), {"jenis": "uang_muka_pelanggan", "kontak": str(PEL)})
    assert rekam["rencana"][-1][0].payment_method == "cash"


def test_dp_jurnal_pratinjau_debit_rekening_kredit_uang_muka_pelanggan(monkeypatch):
    _stub_dp(monkeypatch)
    h = _prev(_K(_m()), {"jenis": "uang_muka_pelanggan", "kontak": str(PEL)})
    assert h["blocks"] == [] and [(str(a), d, k) for a, d, k, _ in h["baris"]] == [(str(COA_BANK), D("500000.00"), D(0)), (str(AKUN_DP), D(0), D("500000.00"))]


def test_dp_tulis_urutan_set_tenant_penulis_cari_bt_dari_jurnal_lalu_tautan(monkeypatch):
    rekam = _stub_dp(monkeypatch)
    k = _K(_m(), bt_jurnal=(BT,))
    body = {"jenis": "uang_muka_pelanggan", "kontak": str(PEL)}
    body["versi"] = _prev(_K(_m()), body)["versi"]
    r = _tulis(k, "catat", body)
    u = k.urut
    assert u.index("kunci:MUTASI_BANK") < u.index("baca_mutasi") < u.index("set_tenant") < u.index("rencana_buat_dp") < u.index("buat_dp_core") < u.index("tautan") < u.index("putus") < u.index("riwayat")
    sql_bt, a_bt = next((s, a) for s, a in k.sql if s.startswith("SELECT id FROM bank_transactions"))
    assert a_bt[0] == T and a_bt[2] == REK and "journal_id" in sql_bt and "status = 'POSTED'" in sql_bt
    t_arg = next(a for s, a in k.sql if s.startswith("INSERT INTO mutasi_bank_tautan"))
    assert t_arg[1:4] == (MID, BT, D("500000.00")) and "DEP-2611-0001" in r["kalimat"] and len(rekam["inti"]) == 1


def test_dp_bt_hasil_tak_ditemukan_atau_ganda_membatalkan_semua(monkeypatch):
    _stub_dp(monkeypatch)
    for bts in ((), (BT, BT_LAWAN)):
        k = _K(_m(), bt_jurnal=bts)
        e = _galat(k, "catat", {"jenis": "uang_muka_pelanggan", "kontak": str(PEL)})
        assert e.status_code == 409 and _kodeg(e) == "BT_HASIL_TAK_DITEMUKAN" and "putus" not in k.urut


# ====================================================================== TERIMA PEMBAYARAN
def _stub_bayar(monkeypatch, blocks=None, unapplied=D(0)):
    rekam = {"ro": [], "inti": []}

    async def ro(conn, ctx, body):
        rekam["ro"].append(body)
        return {"blocks": list(blocks or []), "cek": {"allocated_amount": D(body.total_amount) - unapplied, "unapplied_amount": unapplied}, "payment_number": "RCV-2026-0001",
                "overpayment_deposit_number": "OVP-2611-0001" if unapplied > 0 else None, "invoices": [{"invoice_number": "INV-1", "remaining_after": 0.0}],
                "journals": [{"description": "Penerimaan", "lines": [{"account_code": "1-10201", "account_name": "BCA", "debit": 500000.0, "credit": 0.0},
                                                                    {"account_code": "1-10400", "account_name": "Piutang", "debit": 0.0, "credit": 500000.0}]}]}

    async def inti(conn, ctx, body):
        conn.urut.append("buat_penerimaan")
        rekam["inti"].append(body)
        return {"success": True, "data": {"id": str(PAY), "payment_number": "RCV-2026-0001", "journal_id": "00000000-0000-0000-0000-0000000000bb", "unapplied_amount": unapplied,
                                          "allocated_amount": D(body.total_amount) - unapplied}}
    monkeypatch.setattr(RP, "rencana_penerimaan_ro", ro)
    monkeypatch.setattr(RP, "buat_penerimaan", inti)
    return rekam


ALOK = [{"invoice_id": str(FAKTUR), "nominal": "500000"}]


def test_bayar_validasi_masuk_kontak_alokasi_dan_batas_jumlah(monkeypatch):
    _stub_bayar(monkeypatch)
    assert _kode(_prev(_K(_m(arah="keluar")), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": ALOK})) == ["JENIS_TAK_SESUAI_ARAH"]
    assert _kode(_prev(_K(_m()), {"jenis": "terima_pembayaran", "alokasi": ALOK})) == ["KONTAK_WAJIB"]
    assert _kode(_prev(_K(_m()), {"jenis": "terima_pembayaran", "kontak": str(PEL)})) == ["ALOKASI_WAJIB"]
    assert _kode(_prev(_K(_m()), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": [{"invoice_id": str(FAKTUR), "nominal": "600000"}]})) == ["ALOKASI_MELEBIHI_NOMINAL"]
    assert _kode(_prev(_K(_m()), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": [{"invoice_id": "bukan-uuid", "nominal": "1"}]})) == ["ALOKASI_TAK_SAH"]


def test_bayar_isian_penulis_bank_account_id_adalah_coa_dan_idempotensi_berurutan(monkeypatch):
    rekam = _stub_bayar(monkeypatch)
    _prev(_K(_m(), jumlah_tautan=1), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": ALOK, "catatan": "lunas INV-1"})
    b = rekam["ro"][-1]
    assert (b.customer_id, b.customer_name, b.payment_date, b.payment_method, b.bank_account_id, b.bank_account_name, b.total_amount, b.source_type, b.notes, b.save_as_draft) == \
        (str(PEL), "PT Sumber Rejeki", date(2026, 10, 10), "bank_transfer", str(COA_BANK), "BCA UJI", D("500000.00"), "cash", "lunas INV-1", False)
    assert [(a.invoice_id, a.amount_applied) for a in b.allocations] == [(str(FAKTUR), D("500000"))] and b.idempotency_key == f"BANKFEED:{MID}:2"


def test_bayar_pratinjau_menampilkan_kelebihan_jadi_uang_muka_dan_faktur_sesudah(monkeypatch):
    _stub_bayar(monkeypatch, unapplied=D("100000"))
    h = _prev(_K(_m()), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": [{"invoice_id": str(FAKTUR), "nominal": "400000"}]})
    assert h["blocks"] == [] and h["kelebihan"]["jumlah"] == "100000.00" and h["kelebihan"]["nomor"] == "OVP-2611-0001"
    assert "uang muka" in h["kalimat"].lower() and "Rp 100.000" in h["kalimat"] and h["faktur_sesudah"][0]["invoice_number"] == "INV-1"
    assert [j["account_name"] for j in h["jurnal_ringkas"]] == ["BCA", "Piutang"]


def test_bayar_tanpa_kelebihan_tak_menampilkan_uang_muka(monkeypatch):
    _stub_bayar(monkeypatch)
    h = _prev(_K(_m()), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": ALOK})
    assert h["kelebihan"] is None and "uang muka" not in h["kalimat"].lower()


def test_bayar_blok_penentu_diteruskan_dan_tulis_urutan(monkeypatch):
    _stub_bayar(monkeypatch, blocks=[{"code": "RP_INVOICE_OTHER_CUSTOMER", "message": "milik pelanggan lain"}])
    assert "RP_INVOICE_OTHER_CUSTOMER" in _kode(_prev(_K(_m()), {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": ALOK}))
    rekam = _stub_bayar(monkeypatch)
    k = _K(_m(), bt_jurnal=(BT,))
    body = {"jenis": "terima_pembayaran", "kontak": str(PEL), "alokasi": ALOK}
    body["versi"] = _prev(_K(_m()), body)["versi"]
    r = _tulis(k, "catat", body)
    u = k.urut
    assert u.index("kunci:MUTASI_BANK") < u.index("baca_mutasi") < u.index("set_tenant") < u.index("buat_penerimaan") < u.index("tautan") < u.index("putus") < u.index("riwayat")
    assert "RCV-2026-0001" in r["kalimat"] and len(rekam["inti"]) == 1


# ====================================================================== JENIS yang masih belum ada
def test_hanya_bayar_tagihan_yang_masih_belum_tersedia():
    assert BA.JENIS_BELUM == ("bayar_tagihan",) and set(BA.JENIS_CATAT) == {"uang_masuk", "uang_keluar", "pribadi", "transfer", "uang_muka_pelanggan", "terima_pembayaran"}
    assert _kode(_prev(_K(_m()), {"jenis": "bayar_tagihan"})) == ["JENIS_BELUM_TERSEDIA"]


# ====================================================================== BATALKAN generik: dispatch menurut jenis dokumen
def _stub_batal(monkeypatch, blocks=None):
    rekam = {"panggil": []}

    async def r_trf(conn, tid, trf_id, alasan, bl, kunci=False):
        conn.urut.append("rencana_void_transfer")
        rekam["panggil"].append(("rencana_void_transfer", trf_id, kunci))
        for b in blocks or []:
            bl.galat(b["code"], b.get("status", 400), b["message"])
        return {"blocks": bl.daftar, "t": {"transfer_number": "TRF-2611-0001"}, "hapus_draf": False, "baris": [(COA_BANK, D(0), D("500000"), "b")], "saldo": []}

    async def t_trf(conn, ctx, trf_id, alasan, r):
        conn.urut.append("void_transfer_core")
        rekam["panggil"].append(("void_transfer_core", trf_id, alasan))
        return {"reversal_journal_id": uuid.uuid4()}

    async def kunci_trf(conn, trf_id):
        conn.urut.append("kunci:BANK_TRANSFER_VOID")

    async def t_dp(conn, ctx, dep_id, body):
        conn.urut.append("void_deposit_core")
        rekam["panggil"].append(("void_deposit_core", dep_id, body.reason))
        return {"success": True}

    async def r_pay(conn, ctx, pay_id, alasan, wajib_alasan=False, kunci=True):
        conn.urut.append("rencana_void_pembayaran")
        rekam["panggil"].append(("rencana_void_pembayaran", pay_id, kunci))
        return {"blocks": list(blocks or []), "payment": {"payment_number": "RCV-2026-0001"}}

    async def t_pay(conn, ctx, pay_id, alasan, r):
        conn.urut.append("tulis_void_pembayaran")
        rekam["panggil"].append(("tulis_void_pembayaran", pay_id, alasan))
        return {"success": True}

    async def r_dp(conn, ctx, dep_id, aksi, body):
        return list(blocks or [])
    monkeypatch.setattr(KT, "rencana_void_transfer", r_trf)
    monkeypatch.setattr(BTR, "void_transfer_core", t_trf)
    monkeypatch.setattr(BTR, "kunci_void_transfer", kunci_trf)
    monkeypatch.setattr(CD, "void_deposit_core", t_dp)
    monkeypatch.setattr(CD, "_rencana_dp", r_dp)
    monkeypatch.setattr(RP, "_rencana_void_pembayaran", r_pay)
    monkeypatch.setattr(RP, "_tulis_void_pembayaran", t_pay)
    return rekam


def _dok(source_type, ref_id=None, source_id=None):
    return {"bt_id": BT, "source_type": source_type, "source_id": str(source_id or BT), "nomor_jurnal": "JU-1", "reference_type": None, "reference_id": ref_id}


def _tt():
    return {"id": uuid.uuid4(), "bank_transaction_id": BT, "nominal_alokasi": D("500000.00"), "nomor": "X-1", "jenis": "x", "rekon": False, "kontak_nama": None}


@pytest.mark.parametrize("source_type,ref,sid,urutan,panggil", [
    ("BANK_TRANSFER", None, TRF, ["kunci:BANK_TRANSFER_VOID", "rencana_void_transfer", "void_transfer_core"], ("void_transfer_core", TRF, "salah")),
    ("CUSTOMER_DEPOSIT", None, DEP, ["void_deposit_core"], ("void_deposit_core", DEP, "salah")),
    ("RECEIVE_PAYMENT", None, PAY, ["rencana_void_pembayaran", "tulis_void_pembayaran"], ("tulis_void_pembayaran", PAY, "salah")),
])
def test_batalkan_dispatch_ke_penulis_void_menurut_jenis_dokumen(monkeypatch, source_type, ref, sid, urutan, panggil):
    rekam = _stub_batal(monkeypatch)
    k = _K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok(source_type, ref, sid))
    body = {"alasan": "salah"}
    body["versi"] = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok(source_type, ref, sid)), T, REK, MID, body, Blok(True), False, None))["versi"]
    _tulis(k, "batalkan", body)
    u = k.urut
    assert u.index("lepas_tautan") < min(u.index(x) for x in urutan) and [u.index(x) for x in urutan] == sorted(u.index(x) for x in urutan) and u.index(urutan[-1]) < u.index("kembali") < u.index("riwayat")
    assert tuple(str(x) for x in panggil) in [tuple(str(x) for x in c) for c in rekam["panggil"]]


def test_batalkan_jenis_dokumen_tak_dikenal_diblok(monkeypatch):
    _stub_batal(monkeypatch)
    h = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok("SALES_INVOICE")), T, REK, MID, {"alasan": "salah"}, Blok(True), False, None))
    assert _kode(h) == ["JENIS_DOKUMEN_TAK_DIKENAL"]


def test_batalkan_pratinjau_dp_dan_pembayaran_meneruskan_blok_penentu_dan_menukar_jurnal_asli(monkeypatch):
    _stub_batal(monkeypatch, blocks=[{"code": "PERIODE_TERTUTUP", "message": "ditutup", "status": 400}])
    for st in ("CUSTOMER_DEPOSIT", "RECEIVE_PAYMENT"):
        h = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok(st, None, DEP)), T, REK, MID, {"alasan": "salah"}, Blok(True), False, None))
        assert "PERIODE_TERTUTUP" in _kode(h)


# ====================================================================== IZIN modul tujuan
def _izin(monkeypatch, boleh):
    diminta = []

    async def izin_rute_pengguna(user, jalur, method="GET"):
        diminta.append((tuple(jalur), method))
        return {j: j in boleh for j in jalur}
    monkeypatch.setattr(BA, "izin_rute_pengguna", izin_rute_pengguna, raising=False)
    return diminta


@pytest.mark.parametrize("jenis,jalur,body", [
    ("transfer", "/api/bank-transfers", {"rekening_lawan": str(LAWAN)}),
    ("uang_muka_pelanggan", "/api/customer-deposits", {"kontak": str(PEL)}),
    ("terima_pembayaran", "/api/receive-payments", {"kontak": str(PEL), "alokasi": ALOK}),
])
def test_catat_butuh_izin_buat_modul_tujuan(monkeypatch, jenis, jalur, body):
    _stub_transfer(monkeypatch)
    _stub_dp(monkeypatch)
    _stub_bayar(monkeypatch)
    ctx = {**CTX, "user": {"user_id": USER, "tenant_id": T}}
    diminta = _izin(monkeypatch, set())
    h = _prev(_K(_m(arah="keluar" if jenis == "transfer" else "masuk")), {"jenis": jenis, **body}, ctx)
    assert _kode(h)[:1] == ["IZIN_DITOLAK"] and ((jalur,), "POST") in diminta
    _izin(monkeypatch, {jalur})
    assert "IZIN_DITOLAK" not in _kode(_prev(_K(_m(arah="keluar" if jenis == "transfer" else "masuk")), {"jenis": jenis, **body}, ctx))


def test_batalkan_butuh_izin_void_modul_dokumen(monkeypatch):
    _stub_batal(monkeypatch)
    ctx = {**CTX, "user": {"user_id": USER, "tenant_id": T}}
    diminta = _izin(monkeypatch, set())
    h = asyncio.run(BA.RENCANA["batalkan"](_K(_m(status="dicatat", cara="catat"), tautan=[_tt()], dok=_dok("RECEIVE_PAYMENT", None, PAY)), T, REK, MID, {"alasan": "salah"}, Blok(True), False, ctx))
    assert "IZIN_DITOLAK" in _kode(h) and ((f"/api/receive-payments/{PAY}/void",), "POST") in diminta


def test_tanpa_konteks_pengguna_izin_tidak_diperiksa_di_unit_orkestrasi(monkeypatch):
    _stub_transfer(monkeypatch)
    diminta = _izin(monkeypatch, set())
    assert "IZIN_DITOLAK" not in _kode(_prev(_K(_m(arah="keluar")), {"jenis": "transfer", "rekening_lawan": str(LAWAN)}))
    assert diminta == []
