"""penyesuaian-2 (putusan pemilik LANGSUNG 10 Nov, TESTER #7a): akun lawan jurnal penyesuaian stok = PILIHAN pengguna, divalidasi SERVER (ikut
Accurate): ditolak berkalimat = akun persediaan, induk, nonaktif, piutang/utang, kas/bank; kosong = akun peran bawaan. SATU predikat
(SS.alasan_tolak_lawan) untuk validasi tulis, pratinjau, dan dropdown. Dipakai oleh posting dokumen (+pratinjau) DAN quick adjust items.py."""
import asyncio
import inspect
import re
import uuid
from decimal import Decimal as D

import pytest

from app.routers import items as IT
from app.routers import stock_adjustments as SA
from app.services import stok_sesuai as SS
from app.services.bayar_keluar_rencana import Blok
from app.services.galat_berkode import GalatBerkode
from .test_persediaan_standar_so import AK_ADJ, AK_PERS, SA_ID, T, Kon  # noqa: F401

PILIH = uuid.uuid4()


def akun(**kw):
    a = {"id": PILIH, "account_code": "6-10900", "name": "Biaya Selisih Stok", "account_type": "EXPENSE", "is_header": False,
         "is_active": True, "is_cash": False, "psak_sub_category": None}
    a.update(kw)
    return a


def jalan(aw):
    return asyncio.run(aw)


class KonAkun(Kon):
    """Kon + tabel chart_of_accounts tiruan (tersaring tenant: placeholder $1)."""

    def __init__(self, daftar, **kw):
        super().__init__(**kw)
        self.daftar = daftar

    async def fetchrow(self, sql, *a):
        if "FROM chart_of_accounts c WHERE c.tenant_id = $1 AND c.id = $2" in sql:
            self._cocok(sql, a)
            assert a[0] == T  # SELALU tersaring tenant
            return next((x for x in self.daftar if x["id"] == a[1]), None)
        return await super().fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        if "FROM chart_of_accounts c WHERE c.tenant_id = $1 ORDER BY" in sql:
            self._cocok(sql, a)
            assert a[0] == T
            return self.daftar
        return await super().fetch(sql, *a)


@pytest.fixture(autouse=True)
def _peran(monkeypatch):
    from app.services.role_resolver import AccountRoleUnmappedError

    async def peran(conn, tid, role):
        if not getattr(conn, "akun", True):
            raise AccountRoleUnmappedError("x")
        return AK_PERS if "MERCHANDISE" in str(role) else AK_ADJ
    monkeypatch.setattr(SS, "resolve_account_id_by_role", peran)


@pytest.mark.parametrize("ubah,harap", [
    ({}, None),
    ({"is_header": True}, "akun induk"),
    ({"is_active": False}, "nonaktif"),
    ({"psak_sub_category": "INVENTORY"}, "akun persediaan"),
    ({"id": AK_PERS}, "akun persediaan"),
    ({"account_type": "RECEIVABLE"}, "piutang/utang"),
    ({"account_type": "payable"}, "piutang/utang"),
    ({"is_cash": True}, "kas/bank"),
])
def test_predikat_alasan_tolak(ubah, harap):
    a = SS.alasan_tolak_lawan(akun(**ubah), AK_PERS)
    assert (a is None) if harap is None else (harap in a and "6-10900" in a or "Biaya Selisih Stok" in a)


def test_akun_lawan_kosong_dan_sah_dan_ditolak():
    k = KonAkun([akun()])
    bl = Blok(True)
    assert jalan(SS.akun_lawan(k, T, None, bl)) is None and jalan(SS.akun_lawan(k, T, "", bl)) is None and bl.daftar == []
    assert jalan(SS.akun_lawan(k, T, str(PILIH), bl, AK_PERS)) == PILIH and bl.daftar == []
    bl = Blok(True)
    assert jalan(SS.akun_lawan(KonAkun([akun(is_cash=True)]), T, str(PILIH), bl, AK_PERS)) is None
    assert [b["code"] for b in bl.daftar] == ["AKUN_LAWAN_DITOLAK"] and "kas/bank" in bl.daftar[0]["message"]
    for nilai in ("bukan-uuid", str(uuid.uuid4())):  # tak berformat / bukan akun tenant ini -> tak dikenal (bukan 500, bukan bocor)
        bl = Blok(True)
        assert jalan(SS.akun_lawan(k, T, nilai, bl)) is None and [b["code"] for b in bl.daftar] == ["AKUN_LAWAN_TAK_SAH"]


def test_akun_lawan_tulis_melempar_422_berkode():
    with pytest.raises(GalatBerkode) as e:
        jalan(SS.akun_lawan(KonAkun([akun(account_type="RECEIVABLE")]), T, str(PILIH), Blok(False), AK_PERS))
    assert e.value.status_code == 422 and e.value.code == "AKUN_LAWAN_DITOLAK"


def test_posting_memakai_akun_pilihan_dan_bawaan_bila_kosong():
    bawaan = jalan(SS.rencana_posting(KonAkun([akun()]), T, SA_ID, Blok(True)))
    assert bawaan["akun"] == (AK_PERS, AK_ADJ) and bawaan["baris"][0][0] == AK_ADJ        # decrease: Dr penyesuaian
    r = jalan(SS.rencana_posting(KonAkun([akun()]), T, SA_ID, Blok(True), counter_account_id=str(PILIH)))
    assert r["blocks"] == [] and r["akun"] == (AK_PERS, PILIH)
    assert [(b[0], b[1], b[2]) for b in r["baris"]] == [(PILIH, D("3000"), D("0")), (AK_PERS, D("0"), D("3000"))]  # angka sama, hanya lawan
    assert r["total"] == bawaan["total"] and r["gerak"] == bawaan["gerak"]               # gerak stok TAK berubah oleh pilihan akun


def test_posting_pilihan_ditolak_jadi_blok_pratinjau():
    r = jalan(SS.rencana_posting(KonAkun([akun(is_header=True)]), T, SA_ID, Blok(True), counter_account_id=str(PILIH)))
    assert [b["code"] for b in r["blocks"]] == ["AKUN_LAWAN_DITOLAK"] and r["akun"] == (AK_PERS, AK_ADJ)


def test_dropdown_hanya_akun_sah_dengan_bawaan():
    daftar = [akun(id=AK_ADJ, account_code="5-50100", name="Penyesuaian Persediaan"), akun(), akun(id=uuid.uuid4(), is_header=True),
              akun(id=uuid.uuid4(), is_cash=True), akun(id=uuid.uuid4(), is_active=False), akun(id=AK_PERS, psak_sub_category="INVENTORY"),
              akun(id=uuid.uuid4(), account_type="PAYABLE")]
    d = jalan(SS.daftar_akun_lawan(KonAkun(daftar), T))
    assert [x["account_code"] for x in d["akun"]] == ["5-50100", "6-10900"] and d["bawaan_id"] == str(AK_ADJ)
    assert [x["bawaan"] for x in d["akun"]] == [True, False]
    d = jalan(SS.daftar_akun_lawan(KonAkun(daftar, akun=False), T))      # peran belum diatur: daftar tetap, tanpa bawaan
    assert d["bawaan_id"] is None and not any(x["bawaan"] for x in d["akun"])


def test_rute_dropdown_sebelum_rute_id_dan_post_membawa_pilihan():
    jalur = [getattr(r, "path", "") for r in SA.router.routes]  # prefix /api/stock-adjustments dipasang di main.py
    assert jalur.index("/counter-accounts") < jalur.index("/{adjustment_id}")  # literal sebelum parameter, kalau tidak 422 UUID
    src = inspect.getsource(SA.post_stock_adjustment)
    assert "counter_account_id=lawan" in src and '{"counter_account_id": lawan} if lawan else {}' in src   # idempotensi: pilihan beda != replay
    assert "counter_account_id=lawan" in inspect.getsource(SA.preview_post_stock_adjustment)


def test_quick_adjust_validasi_pilihan_sebelum_tulisan_apa_pun():
    src = inspect.getsource(IT.create_stock_adjustment)
    iv = src.index("SS_PEN.akun_lawan(")
    assert iv < src.index("INSERT INTO journal_entries") and iv < src.index("record_inventory_inbound")
    assert "akun_lawan_pilihan or await resolve_account_id(" in src                  # kosong = bawaan 5-50100 (perilaku lama)
    assert len(re.findall(r"SS_PEN\.akun_lawan\(", src)) == 1
