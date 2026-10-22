"""P-U3b (22 Okt 2026) sisi TULIS Uang Muka Pembelian: lepas penerapan (unapply), idempotensi, nominal 2dp, void sesudah lepas,
sisa tagihan = compute_ap_outstanding, buka parkir by-vendor/available, V437/V438."""
import asyncio
import inspect
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import vendor_deposits as VD
from app.schemas import vendor_deposits as VS
from app.services import idem_buat, uang_muka_vendor as UMV, uang_muka_vendor_daftar as UMD
from app.services.role_resolver import AccountRole
from app.utils import idempotency as IDM

MIG = Path(__file__).resolve().parents[3] / "migrations"
T = "kaos-biru-konveksi"
DID, APP_ID, BILL, J_ASLI = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
AP, PREPAID, KAS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class Kon:
    def __init__(self, app_status="active", asli=None, baris=None, sisa=Decimal("400"), sisa_tagihan=Decimal("600"),
                 periode=None, vd=True, app=True, bergantung=(0, 0), tandai="UPDATE 1"):
        self.app_status, self.sisa, self.sisa_tagihan, self.periode = app_status, sisa, sisa_tagihan, periode
        self.asli = asli if asli is not None else {"id": J_ASLI, "status": "POSTED", "reversed_by_id": None}
        self.baris = baris if baris is not None else [
            {"account_id": AP, "debit": Decimal("600.50"), "credit": Decimal("0"), "memo": "Apply"},
            {"account_id": PREPAID, "debit": Decimal("0"), "credit": Decimal("600.50"), "memo": "Apply"}]
        self.vd, self.app, self.bergantung, self.tandai = vd, app, bergantung, tandai
        self.log, self.args = [], []

    def transaction(self): return _Tx()

    def _l(self, k, sql, a):
        q = " ".join(sql.split()); self.log.append((k, q)); self.args.append((q, a)); return q

    async def execute(self, sql, *a):
        q = self._l("exec", sql, a)
        if q.startswith("UPDATE journal_entries SET reversed_by_id"):
            return self.tandai
        return "UPDATE 1"

    async def fetchrow(self, sql, *a):
        q = self._l("fetchrow", sql, a)
        if "FROM vendor_deposits" in q:
            return {"id": DID, "deposit_number": "VD-1", "status": "partial"} if self.vd else None
        if "FROM vendor_deposit_applications a" in q:
            if not self.app:
                return None
            return {"id": APP_ID, "bill_id": BILL, "amount": Decimal("600.50"), "journal_id": J_ASLI, "status": self.app_status,
                    "reversed_by_id": uuid.uuid4() if self.app_status == "reversed" else None, "bill_number": "BILL-1"}
        if "FROM bills" in q:
            return {"id": BILL}
        if "FROM journal_entries" in q:
            return self.asli
        if "fiscal_periods" in q:
            return self.periode
        if "n_terap" in q:
            return {"n_terap": self.bergantung[0], "n_refund": self.bergantung[1]}
        return None

    async def fetch(self, sql, *a):
        self._l("fetch", sql, a)
        return self.baris

    async def fetchval(self, sql, *a):
        q = self._l("fetchval", sql, a)
        if "get_next_journal_number" in q: return "JV-2610-0009"
        if "INSERT INTO journal_entries" in q: return uuid.uuid4()
        if "SUM(jl.debit - jl.credit)" in q: return self.sisa
        if "compute_ap_outstanding" in q: return self.sisa_tagihan
        if "SELECT journal_number" in q: return "JV-2610-0001"
        return None


def _pool(monkeypatch, kon):
    class CM:
        async def __aenter__(s): return kon
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    monkeypatch.setattr(VD, "get_pool", gp)


@pytest.fixture
def lingkungan(monkeypatch):
    async def akun(conn, tid, p): return {AccountRole.AP_TRADE: AP, AccountRole.AP_PREPAID: PREPAID, AccountRole.CASH_GENERAL: KAS}[p]
    monkeypatch.setattr(UMV, "akun_peran", akun)
    seg, riw = [], []
    async def segarkan(conn, tid, bid): seg.append(bid)
    async def catat(*a, **k): riw.append((a, k))
    async def hari(conn, tid): return date(2026, 10, 22)
    # 22 Okt: penulis lepas pindah ke services/uang_muka_vendor_lepas (SATU penulis; dipakai juga void faktur BACKEND)
    from app.services import uang_muka_vendor_lepas as L
    import app.utils.tanggal_tenant as TT
    monkeypatch.setattr(L, "segarkan_cache_hutang_tagihan", segarkan)
    monkeypatch.setattr(L, "catat_riwayat", catat)
    monkeypatch.setattr(TT, "tanggal_dokumen", hari)
    monkeypatch.setattr(VD, "tanggal_dokumen", hari)
    return {"seg": seg, "riw": riw}


def _lepas(monkeypatch, kon):
    _pool(monkeypatch, kon)
    return asyncio.run(VD.unapply_vendor_deposit(_Req(), DID, APP_ID))


# ---------------------------------------------------------------- lepas penerapan
def test_lepas_jurnal_pembalik_opsi_b_dan_tautan(monkeypatch, lingkungan):
    kon = Kon()
    r = _lepas(monkeypatch, kon)
    ins = [(q, a) for q, a in kon.args if q.startswith("INSERT INTO journal_entries")]
    assert len(ins) == 1
    q, a = ins[0]
    assert "'DEPOSIT_APPLICATION'" in q and "reversal_of_id" in q and "'DRAFT'" in q
    assert a[4] == BILL, "source_id jurnal lepas = TAGIHAN (opsi B: Cr Utang wajib bersumber kewajiban)"
    assert a[5] == J_ASLI and a[6] == Decimal("600.50")
    baris = [a for q, a in kon.args if q.startswith("INSERT INTO journal_lines")]
    assert [(b[2], b[3], b[4]) for b in baris] == [(AP, Decimal("0"), Decimal("600.50")), (PREPAID, Decimal("600.50"), Decimal("0"))]
    urut = [q for k, q in kon.log if k == "exec"]
    i_post = next(i for i, q in enumerate(urut) if q.startswith("UPDATE journal_entries SET status = 'POSTED'"))
    i_tanda = next(i for i, q in enumerate(urut) if q.startswith("UPDATE journal_entries SET reversed_by_id"))
    assert i_post < i_tanda and "reversed_by_id IS NULL" in urut[i_tanda]
    assert any(q.startswith("UPDATE vendor_deposit_applications SET status = 'reversed'") and "status = 'active'" in q for q in urut)
    assert lingkungan["seg"] == [BILL] and len(lingkungan["riw"]) == 1
    d = r["data"]
    assert r["message"] == "Penerapan uang muka dilepas." and d["status"] == "reversed"
    assert d["amount"] == 600.5 and d["deposit_remaining"] == 400.0 and d["bill_remaining"] == 600.0
    assert d["reversal_journal_number"] == "JV-2610-0009"


def test_lepas_kunci_saldo_sebelum_baca(monkeypatch, lingkungan):
    kon = Kon()
    _lepas(monkeypatch, kon)
    i_lock = next(i for i, (k, q) in enumerate(kon.log) if "VENDOR_DEPOSIT_SALDO" in str(kon.args[i][1]))
    i_baca = next(i for i, (k, q) in enumerate(kon.log) if "FROM vendor_deposits" in q)
    assert i_lock < i_baca and "FOR UPDATE" in kon.log[i_baca][1]
    # bacaan awal tanpa kunci hanya mencari tagihan (dikunci DULU, urutan global); bacaan berkunci sesudah SALDO
    i_app = next(i for i, (k, q) in enumerate(kon.log) if "FROM vendor_deposit_applications a" in q and "FOR UPDATE OF a" in q)
    assert i_lock < i_app


def test_lepas_kedua_idempoten_tanpa_jurnal_baru(monkeypatch, lingkungan):
    kon = Kon(app_status="reversed")
    r = _lepas(monkeypatch, kon)
    assert r["message"] == "Penerapan uang muka sudah dilepas sebelumnya." and r["data"]["status"] == "reversed"
    assert r["data"]["reversal_journal_number"] == "JV-2610-0001"
    assert not any(q.startswith("INSERT") or q.startswith("UPDATE") for k, q in kon.log)
    assert lingkungan["seg"] == [] and lingkungan["riw"] == []


@pytest.mark.parametrize("kw,kode", [({"vd": False}, 404), ({"app": False}, 404)])
def test_lepas_404(monkeypatch, lingkungan, kw, kode):
    with pytest.raises(HTTPException) as e:
        _lepas(monkeypatch, Kon(**kw))
    assert e.value.status_code == kode


def test_lepas_jurnal_sudah_dibalik_atau_tak_ada_409(monkeypatch, lingkungan):
    for asli, kode in (({"id": J_ASLI, "status": "POSTED", "reversed_by_id": uuid.uuid4()}, "JURNAL_PENERAPAN_SUDAH_DIBALIK"),
                       ({"id": J_ASLI, "status": "DRAFT", "reversed_by_id": None}, "JURNAL_PENERAPAN_TIDAK_ADA")):
        kon = Kon(asli=asli)
        with pytest.raises(HTTPException) as e:
            _lepas(monkeypatch, kon)
        assert e.value.status_code == 409 and e.value.detail["code"] == kode
        assert not any(q.startswith("INSERT INTO journal_entries") for k, q in kon.log)


def test_lepas_balapan_penanda_gagal_409(monkeypatch, lingkungan):
    with pytest.raises(HTTPException) as e:
        _lepas(monkeypatch, Kon(tandai="UPDATE 0"))
    assert e.value.detail["code"] == "JURNAL_PENERAPAN_SUDAH_DIBALIK"


def test_lepas_periode_tertutup_409(monkeypatch, lingkungan):
    kon = Kon(periode={"status": "CLOSED"})
    with pytest.raises(HTTPException) as e:
        _lepas(monkeypatch, kon)
    assert e.value.status_code == 409 and e.value.detail["code"] == "PERIODE_TERTUTUP"
    assert not any(q.startswith("INSERT INTO journal_entries") for k, q in kon.log)


def test_lepas_jurnal_timpang_ditolak_tanpa_tulis(monkeypatch, lingkungan):
    kon = Kon(baris=[{"account_id": AP, "debit": Decimal("600.50"), "credit": Decimal("0"), "memo": None},
                     {"account_id": PREPAID, "debit": Decimal("0"), "credit": Decimal("600.00"), "memo": None}])
    with pytest.raises(HTTPException) as e:
        _lepas(monkeypatch, kon)
    assert e.value.status_code == 500
    assert not any(q.startswith("INSERT") for k, q in kon.log)


# ---------------------------------------------------------------- void sesudah lepas
def test_void_hanya_ditahan_penerapan_aktif_atau_refund():
    q = " ".join(VD.SQL_BERGANTUNG.split())
    assert "status = 'active'" in q and "vendor_deposit_refunds" in q
    src = inspect.getsource(VD.void_vendor_deposit)
    assert 'vd["applied_amount"]' not in src and "SQL_BERGANTUNG" in src
    assert "VENDOR_DEPOSIT_VOID:{deposit_id}" in src and "VENDOR_DEPOSIT_SALDO:{deposit_id}" in src
    assert src.index("VENDOR_DEPOSIT_VOID:") < src.index("VENDOR_DEPOSIT_SALDO:") < src.index("FOR UPDATE")


def test_kunci_saldo_sama_di_semua_jalur_saldo():
    from app.services import uang_muka_vendor_lepas as L
    for fn in (VD.apply_vendor_deposit, VD.refund_vendor_deposit, VD.void_vendor_deposit, L.lepas_penerapan):
        assert "VENDOR_DEPOSIT_SALDO:{deposit_id}" in inspect.getsource(fn), fn.__name__
    assert "lepas_penerapan(conn, ctx, deposit_id, application_id)" in inspect.getsource(VD.unapply_vendor_deposit)


def test_urutan_kunci_global_tagihan_dulu():
    """Tagihan (FOR UPDATE) SEBELUM kunci SALDO uang muka di /apply dan lepas_penerapan (sama dgn void faktur BACKEND) -> tanpa deadlock silang."""
    from app.services import uang_muka_vendor_lepas as L
    for src in (inspect.getsource(VD.apply_vendor_deposit), inspect.getsource(L.lepas_penerapan)):
        assert src.index("FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE") < src.index("VENDOR_DEPOSIT_SALDO:")


# ---------------------------------------------------------------- idempotensi (Law 14)
@pytest.mark.parametrize("fn,prefix", [("apply_vendor_deposit", "VD_APPLY"), ("refund_vendor_deposit", "VD_REFUND"),
                                       ("post_vendor_deposit", "VD_POST")])
def test_aksi_replay_tanpa_tulisan(monkeypatch, lingkungan, fn, prefix):
    kon = Kon()
    _pool(monkeypatch, kon)
    dipanggil = []
    async def mulai_aksi(conn, ctx, kunci, pfx, doc, isi, response=None):
        dipanggil.append(pfx); return "k", "s", {"replay": True}
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    monkeypatch.setattr(VD, "_kunci_idem", lambda r: "kunci-klien")
    args = {"apply_vendor_deposit": (VS.ApplyDepositRequest(bill_id=BILL, amount="10.50"),),
            "refund_vendor_deposit": (VS.VendorDepositRefundCreate(refund_date=date(2026, 10, 22), amount="10.50"),),
            "post_vendor_deposit": ()}[fn]
    r = asyncio.run(getattr(VD, fn)(_Req(), DID, *args))
    assert r == {"replay": True} and dipanggil == [prefix]
    assert not any(q.startswith("INSERT") or "FROM vendor_deposits" in q for k, q in kon.log), "replay sebelum baca/tulis apa pun"


def test_buat_idempoten_dan_sumber_basi():
    src = inspect.getsource(VD.create_vendor_deposit)
    assert 'idem_buat.mulai(conn, ctx, _kunci_idem(request), "VD_CREATE"' in src and '"VD_CREATE", resp, row["id"]' in src
    assert "conn.transaction()" in src
    assert IDM.SUMBER_BUAT["VD_CREATE"] == ("vendor_deposits", None, ())
    assert idem_buat._TABEL["vendor_deposits"] == "deposit_number"


# ---------------------------------------------------------------- nominal 2 desimal
def test_nominal_request_2_desimal():
    assert VS.ApplyDepositRequest(bill_id=BILL, amount="1000.50").amount == Decimal("1000.50")
    for buruk in ("1000.505", "0", "-1"):
        with pytest.raises(ValidationError):
            VS.ApplyDepositRequest(bill_id=BILL, amount=buruk)
        with pytest.raises(ValidationError):
            VS.VendorDepositRefundCreate(refund_date=date(2026, 10, 22), amount=buruk)
        with pytest.raises(ValidationError):
            VS.VendorDepositCreate(deposit_date=date(2026, 10, 22), vendor_id=uuid.uuid4(), amount=buruk)


def test_nominal_respons_float_bukan_int():
    for model, medan in ((VS.ApplyDepositResponse, ("applied_amount", "deposit_remaining", "bill_remaining")),
                         (VS.VendorDepositResponse, ("amount", "applied_amount", "remaining_amount")),
                         (VS.VendorDepositRefundResponse, ("amount",)), (VS.VendorDepositApplicationResponse, ("amount",)),
                         (VS.AvailableDepositItem, ("amount", "applied_amount", "remaining_amount")),
                         (VS.AvailableDepositsResponse, ("total_available",)),
                         (VS.VendorDepositsForVendorResponse, ("total_deposits", "total_applied", "total_remaining"))):
        for m in medan:
            assert model.model_fields[m].annotation is float, (model.__name__, m)


def test_sisa_tagihan_dari_compute_ap_outstanding_decimal():
    class C:
        async def fetchval(self, sql, *a):
            assert "compute_ap_outstanding($1) WHERE bill_id = $2" in sql and a == (T, BILL)
            return Decimal("1234.56")
    assert asyncio.run(VD.get_bill_remaining_from_journal(C(), T, BILL)) == Decimal("1234.56")
    src = inspect.getsource(VD)
    assert "int(result" not in src


# ---------------------------------------------------------------- baca: penerapan dilepas, buka parkir
def test_sql_aplikasi_membawa_status_dan_bundel_hanya_aktif():
    assert "a.status, a.reversed_by_id, a.reversed_at" in UMD.SQL_APLIKASI
    assert "a.status = 'active'" in inspect.getsource(UMD.kelompok_untuk)


def test_by_vendor_available_tak_diparkir_dan_tak_pakai_fungsi_db_rusak():
    for fn in (VD.get_vendor_deposits_for_vendor, VD.get_available_deposits_for_vendor):
        src = inspect.getsource(fn)
        assert "get_vendor_deposits(" not in src and "get_available_vendor_deposits(" not in src
    src = inspect.getsource(VD)
    assert "fitur_belum_tersedia" not in src
    q = " ".join(VD.sql_tersedia().split())
    assert UMD.sql_aksi(UMD.AKSI_BELUM_DITERAPKAN) in q and "vd.tenant_id = $1 AND vd.vendor_id = $2" in q
    assert "vd.tenant_id = $1 AND vd.vendor_id = $2" in " ".join(VD.SQL_PER_VENDOR.split())


# ---------------------------------------------------------------- migrasi
def test_v437_kolom_lepas_dan_pemicu_numeric():
    s = (MIG / "V437__vendor_deposit_applications_lepas.sql").read_text()
    assert "ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active'" in s and "REFERENCES journal_entries(id)" in s
    fn = s.split("CREATE OR REPLACE FUNCTION public.update_vendor_deposit_status()", 1)[1]
    assert "BIGINT" not in fn and "NUMERIC(18,2)" in fn and "AND status = 'active'" in fn and "ELSE 'posted'" in fn
    rb = (MIG / "V437__vendor_deposit_applications_lepas_ROLLBACK.sql").read_text()
    assert "status <> 'active'" in rb and "DROP COLUMN status" in rb and "v_total_applied BIGINT" in rb


def test_v438_drop_lima_dan_rollback_lima():
    s = (MIG / "V438__drop_fungsi_um_vendor_rusak.sql").read_text()
    nama = ("get_vendor_deposit_applications(uuid)", "get_vendor_deposit_refunds(uuid)", "get_vendor_deposits(uuid, character varying)",
            "get_available_vendor_deposits(uuid)", "get_vendor_deposit_summary(text)")
    for n in nama:
        assert f"DROP FUNCTION IF EXISTS public.{n};" in s
    rb = (MIG / "V438__drop_fungsi_um_vendor_rusak_ROLLBACK.sql").read_text()
    assert len(re.findall(r"CREATE OR REPLACE FUNCTION public\.get_(available_)?vendor_deposit", rb)) == 5
