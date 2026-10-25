"""PPh pembayaran tagihan dihitung SERVER (putusan pemilik LANGSUNG 25 Okt 2026).

Aturan: dasar = nilai tagihan TANPA PPN x (dibayar / total tagihan); beberapa tagihan = jumlah dasar lalu tarif SEKALI; rupiah penuh
HALF_UP; pph_amount klien > 0 yang beda -> blok PPH_TIDAK_SESUAI (penulis menolak), tanpa jenis -> PPH_TANPA_JENIS; jenis wajib
milik tenant + is_withholding + aktif. Penulis memakai angka SERVER; rekaman PPh tax_period "YYYY-MM" (== laporan PPh).
is_withholding ikut di respons /tax-codes (+ /dropdown, /{id}) -- dirilis BERSAMA penolakan beda (MASTER: jangan menghidupkan kotak PPh
lama sebelum server menolak angka klien)."""
import ast
import asyncio
import inspect
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.routers import bill_payments as BP
from app.routers import tax_codes as TC
from app.schemas import tax_codes as TCS
from app.services import bayar_keluar_rencana as BKR
from app.services import bills_service as BS

D = Decimal
TC_ID, TC_COA, ROLE_COA = uuid4(), uuid4(), uuid4()


# ── rumus murni ─────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("total,ppn,bayar,harap", [
    (D("1110000"), D("110000"), D("1110000"), D("1000000")),  # bayar penuh = nilai tanpa PPN penuh
    (D("1110000"), D("110000"), D("555000"), D("500000")),    # setengah
    (D("1000000"), D("0"), D("250000"), D("250000")),         # non-PKP
    (D("0"), D("0"), D("100"), D("0")),                       # total nol -> 0, bukan bagi nol
])
def test_dasar_prorata(total, ppn, bayar, harap):
    assert BKR.dasar_pph_prorata(total, ppn, bayar) == harap


@pytest.mark.parametrize("dasar,tarif,harap", [
    (D("1000000"), D("2"), D("20000")),
    (D("12525"), D("2"), D("251")),      # 250,50 -> 251 (HALF_UP)
    (D("12475"), D("2"), D("250")),      # 249,50 -> 250
    (D("12474"), D("2"), D("249")),      # 249,48 -> 249
    (D("333.33"), D("15"), D("50")),
])
def test_pph_rupiah_penuh_half_up(dasar, tarif, harap):
    assert BKR.pph_dari_dasar(dasar, tarif) == harap


# ── tentukan_pph ────────────────────────────────────────────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, tc=True, coa=True):
        self.tc, self.coa, self.sql = tc, coa, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM tax_codes" in sql and self.tc and a[1] == "t1":
            return {"id": TC_ID, "code": "PPH23-2", "rate": D("2.00"), "coa_id": TC_COA if self.coa else None}
        return None

    async def fetchval(self, sql, *a):
        return None


def _t(conn, kode, klien, bagian, tid="t1"):
    async def peran(conn, tid, role):
        return ROLE_COA
    BKR.resolve_account_id_by_role, lama = peran, BKR.resolve_account_id_by_role
    try:
        return asyncio.run(BKR.tentukan_pph(conn, tid, kode, klien, bagian))
    finally:
        BKR.resolve_account_id_by_role = lama


PENUH = [(D("1110000"), D("110000"), D("1110000"))]


def test_tanpa_jenis_tanpa_nominal_nol_tanpa_blok():
    p = _t(_Conn(), None, 0, PENUH)
    assert p["amount"] == 0 and p["blocks"] == [] and p["tax_code_id"] is None


def test_nominal_tanpa_jenis_diblok():
    assert [b["code"] for b in _t(_Conn(), None, 20000, PENUH)["blocks"]] == ["PPH_TANPA_JENIS"]


@pytest.mark.parametrize("conn,kode,tid", [(_Conn(tc=False), str(TC_ID), "t1"), (_Conn(), "bukan-uuid", "t1"),
                                           (_Conn(), str(TC_ID), "t2")])
def test_jenis_tak_sah_atau_tenant_lain_diblok(conn, kode, tid):
    p = _t(conn, kode, 0, PENUH, tid=tid)
    assert [b["code"] for b in p["blocks"]] == ["PPH_JENIS_TIDAK_SAH"] and p["amount"] == 0


def test_kueri_jenis_berfilter_tenant_withholding_aktif():
    c = _Conn()
    _t(c, str(TC_ID), 0, PENUH)
    sql = c.sql[0][0]
    assert "tenant_id = $2" in sql and "is_withholding" in sql and "is_active" in sql


def test_server_menghitung_klien_kosong_dipakai_server():
    p = _t(_Conn(), str(TC_ID), None, PENUH)
    assert p["amount"] == D("20000") and p["base"] == D("1000000.00") and p["blocks"] == [] and p["coa_id"] == TC_COA
    # penulis lama (record_payment) memanggil UUID(pph_tax_code_id): objek UUID asyncpg -> AttributeError (tertangkap uji nyata)
    assert isinstance(p["tax_code_id"], str) and UUID(p["tax_code_id"]) == TC_ID


def test_klien_sama_lolos_beda_diblok_dengan_angka_server():
    assert _t(_Conn(), str(TC_ID), 20000, PENUH)["blocks"] == []
    p = _t(_Conn(), str(TC_ID), 22200, PENUH)  # rumus lama layar: total termasuk PPN x 2%
    assert [b["code"] for b in p["blocks"]] == ["PPH_TIDAK_SESUAI"]
    assert "Rp 20.000" in p["blocks"][0]["message"] and "(" not in p["blocks"][0]["message"]
    assert p["amount"] == D("20000")  # angka yang dipakai tetap server


def test_beberapa_tagihan_dasar_dijumlah_lalu_dibulatkan_sekali():
    # jenis palsu = PPH23-2 (2%); dasar dijumlah dulu, tarif + pembulatan SEKALI
    bagian = [(D("333.33"), D("0"), D("333.33"))] * 2
    assert _t(_Conn(), str(TC_ID), 0, bagian)["amount"] == BKR.pph_dari_dasar(D("666.66"), D("2"))
    tiga = [(D("25"), D("0"), D("25"))] * 3  # 2% x 25 = 0,50 -> per tagihan 1+1+1 = 3; dijumlah 75 x 2% = 1,50 -> 2
    assert _t(_Conn(), str(TC_ID), 0, tiga)["amount"] == D("2")


def test_kontrol_merah_pembulatan_per_tagihan_akan_berbeda():
    tiga = [(D("25"), D("0"), D("25"))] * 3
    per_tagihan = sum(BKR.pph_dari_dasar(BKR.dasar_pph_prorata(*b), D("2")) for b in tiga)
    assert per_tagihan == D("3") != _t(_Conn(), str(TC_ID), 0, tiga)["amount"]


def test_coa_jatuh_ke_peran_bila_kode_tanpa_akun():
    assert _t(_Conn(coa=False), str(TC_ID), 0, PENUH)["coa_id"] == ROLE_COA


# ── penulis memakai angka server ────────────────────────────────────────────────────────────────────────────────

def _atribut(src: str) -> set:
    return {f"{n.value.id}.{n.attr}" for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}


def test_penulis_bill_payments_tak_membaca_pph_klien():
    src = Path(BP.__file__).read_text(encoding="utf-8")
    assert "payload.pph_amount" not in _atribut(src) and "payload.pph_tax_code_id" not in _atribut(src)
    assert src.count('_r["pph"]["amount"]') == 3 and src.count('_r["pph"]["tax_code_id"]') == 3


def test_rencana_dua_jalur_memakai_tentukan_pph():
    for fn in (BKR.rencana_bayar, BKR.rencana_bayar_tagihan):
        s = inspect.getsource(fn)
        assert "await tentukan_pph(" in s, fn.__name__
        assert 'request.get("pph_amount") or 0' not in s and "payload.pph_amount or 0" not in s, fn.__name__


def test_rekaman_pph_tax_period_yyyy_mm_dan_dasar_server():
    efek = inspect.getsource(BP.efek_posting_bayar)
    bayar = inspect.getsource(BS.BillsService.record_payment)
    for s in (efek, bayar):
        assert '"%Y-%m"' in s and '"%Y%m"' not in s
    assert 'pph_dpp = _r["pph"]["base"]' in bayar and "pph_base if pph_base is not None" in efek
    assert "tenant_id = $2" in efek.split("FROM tax_codes")[1][:40]


def test_jurnal_bayar_coa_pph_berfilter_tenant():
    s = inspect.getsource(BKR.baris_jurnal_bayar)
    assert "FROM tax_codes WHERE id = $1 AND tenant_id = $2" in s


# ── is_withholding di respons kode pajak ────────────────────────────────────────────────────────────────────────

def test_is_withholding_di_tiga_respons_kode_pajak():
    for m in (TCS.TaxCodeListItem, TCS.TaxCodeDetail, TCS.TaxCodeDropdownItem):
        assert "is_withholding" in m.model_fields, m.__name__
    src = Path(TC.__file__).read_text(encoding="utf-8")
    assert src.count('"is_withholding": bool(row["is_withholding"])') == 3
    assert src.count("dpp_factor_num, dpp_factor_den, is_withholding") == 3
