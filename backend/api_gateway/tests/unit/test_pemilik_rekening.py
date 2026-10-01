"""Nama pemilik rekening (pemilik lewat MASTER, 28 Sep 2026; V329 bank_accounts.account_holder_name).

Diukur: faktur/proforma/SO/penawaran men-snapshot payment_account_holder = account_name Kas & Bank ('BCA Operasional',
'BCA Pemasukan' — nama LAMA yang kini sudah tak ada di bank_accounts) -> PDF/WA mencetak "a.n. Operasional"/"a.n.
Pemasukan". Dokumen TAK menyimpan bank_account_id; tautannya = (tenant, nomor rekening).
SATU penentu faktur_cetak.pemilik_rekening: account_holder_name -> snapshot bila tak tampak nama akun -> None.
"""
import inspect
import os

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import proformas as PF  # noqa: E402
from app.routers import quotes as Q  # noqa: E402
from app.routers import sales_invoices as SI  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402
from app.services import dashboard_v2 as DV  # noqa: E402
from app.services import faktur_cetak as FC  # noqa: E402

NAMA_AKUN = ["BCA Operasional", "BCA Pengeluaran", "Kas Manado", "Liturgi"]


@pytest.mark.parametrize("bank,snap,akun,harap", [
    # (1) nama pemilik di Kas & Bank menang atas snapshot apa pun
    ("Bank BCA", "BCA Operasional", "PT Kaos Biru", "PT Kaos Biru"),
    ("BCA", "Budi Santoso", "  PT  Kaos Biru ", "PT Kaos Biru"),
    # (2) snapshot tampak nama akun internal -> TIDAK dicetak (dulu 'Operasional' / 'Pemasukan')
    ("Bank BCA", "BCA Operasional", None, None),
    ("BCA", "BCA Pemasukan", None, None),                 # nama lama, sudah tak ada di bank_accounts
    ("BCA", "BCA Anthonius Iwan Adhipraja", None, None),  # = account_name grapgrap saat ini
    ("Bank BCA", "Bank BCA Operasional", None, None),
    ("BCA", "BCA", None, None),
    ("BCA", "Liturgi", None, None),                       # sama persis dengan nama akun tenant
    ("BCA", "kas  manado", None, None),                   # beda spasi/huruf tetap dikenali
    # (3) snapshot yang memang nama orang/usaha -> dicetak apa adanya
    ("BCA", "Anthonius Iwan Adhipraja", None, "Anthonius Iwan Adhipraja"),
    ("Mandiri", "BCA Budi", None, "BCA Budi"),
    (None, "Budi", None, "Budi"),
    ("BCA", None, None, None),
    ("BCA", "   ", "", None),
])
def test_prioritas_pemilik_rekening(bank, snap, akun, harap):
    assert FC.pemilik_rekening(bank, snap, akun, NAMA_AKUN) == harap


class _Conn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows


BARIS = [
    {"account_number": "1111222233", "account_name": "BCA Operasional", "account_holder_name": "PT Kaos Biru", "is_active": True},
    {"account_number": "111-122-2233", "account_name": "BCA Lama", "account_holder_name": "Nama Basi", "is_active": False},
    {"account_number": "4371922746", "account_name": "BCA Pengeluaran", "account_holder_name": None, "is_active": True},
    {"account_number": None, "account_name": "Kas Operasional", "account_holder_name": None, "is_active": True},
    {"account_number": "999", "account_name": "BCA Pemasukan", "account_holder_name": "  ", "is_active": False},
]


@pytest.mark.asyncio
async def test_muat_rekening_aktif_menang_nomor_digit_nama_nonaktif_ikut():
    c = _Conn(BARIS)
    rek = await FC.muat_rekening(c, "kaos")
    assert rek["pemilik"] == {"1111222233": "PT Kaos Biru"}   # aktif menang atas nonaktif bernomor sama; kosong dibuang
    assert "BCA Pemasukan" in rek["nama_akun"] and "Kas Operasional" in rek["nama_akun"]
    assert "tenant_id = $1" in c.sql[0][0] and c.sql[0][1] == ("kaos",)


@pytest.mark.asyncio
@pytest.mark.parametrize("bank,nomor,snap,harap", [
    ("Bank BCA", "1111 2222 33", "BCA Operasional", "PT Kaos Biru"),   # nomor dinormalisasi -> pemilik akun
    ("BCA", "4371922746", "BCA Pengeluaran", None),                   # akun belum diisi -> tanpa a.n.
    ("BCA", "999", "BCA Pemasukan", None),                            # nama akun nonaktif tetap internal
    ("BCA", "555", "Sari Dewi", "Sari Dewi"),                         # rekening di luar Kas & Bank
])
async def test_pemilik_cetak_lewat_bank_accounts(bank, nomor, snap, harap):
    assert await FC.pemilik_cetak(_Conn(BARIS), "kaos", bank, nomor, snap) == harap


def _render(dok):
    from app.services.pdf_service import get_pdf_service
    env = get_pdf_service().jinja_env
    return env.from_string('{% with dok = d %}{% include "_partials/rekening_pembayaran.html" %}{% endwith %}').render(d=dok)


def test_partial_hanya_mencetak_hasil_penentu_bukan_snapshot_mentah():
    dasar = {"payment_bank_name": "Bank BCA", "payment_account_number": "1111222233",
             "payment_account_holder": "BCA Operasional"}
    tanpa = _render({**dasar, "rekening_pemilik_cetak": None})
    assert "a.n." not in tanpa and "Operasional" not in tanpa and "1111222233" in tanpa
    dengan = _render({**dasar, "rekening_pemilik_cetak": "PT Kaos Biru"})
    assert "a.n. PT Kaos Biru" in dengan


def test_template_b_memakai_hasil_penentu():
    from pathlib import Path
    t = (Path(FC.__file__).parent.parent / "templates/pdf/sales_invoice_b.html").read_text()
    assert "invoice.rekening_pemilik_cetak" in t and "invoice.payment_account_holder" not in t


# P3 SO-dokumen: konteks PDF dipindah ke pemuat bersama (rute /pdf + render dokumen)
@pytest.mark.parametrize("fungsi", [SI.muat_pdf_faktur, PF.muat_pdf_proforma, Q.muat_pdf_penawaran])
def test_ketiga_pdf_mengisi_rekening_pemilik_cetak_lewat_penentu(fungsi):
    src = inspect.getsource(fungsi)
    assert "muat_rekening(conn" in src and "pemilik_dari(" in src
    assert "rekening_pemilik_cetak" in src


@pytest.mark.parametrize("modul", [SI, SO, Q, PF])
def test_snapshot_saat_dokumen_dibuat_memakai_penentu(modul):
    assert inspect.getsource(modul).count("_fc_snap.pemilik_cetak(conn, ctx[\"tenant_id\"]") == 1


def test_wa_memakai_pemilik_akun_dan_menolak_nama_akun():
    rek = {"pemilik": {"1111222233": "PT Kaos Biru"}, "nama_akun": NAMA_AKUN}
    assert DV.teks_rekening("Bank BCA", "1111222233", "BCA Operasional", rek) == "Bank BCA 1111222233 a.n. PT Kaos Biru"
    assert DV.teks_rekening("BCA", "4371922746", "BCA Pengeluaran", rek) == "BCA 4371922746"
    assert DV.teks_rekening("BCA", "4371922746", "BCA Pengeluaran") == "BCA 4371922746"   # tanpa data rekening pun


@pytest.mark.asyncio
async def test_wa_cadangan_rekening_tenant_tak_mencetak_nama_akun():
    class C:
        def __init__(self, holder):
            self.holder = holder

        async def fetchrow(self, sql, *a):
            assert "account_holder_name" in sql and "tenant_id = $1" in sql
            # nama akun SENGAJA tanpa awalan bank: deteksi nama-akun tak boleh jadi satu-satunya penahan
            return {"bank_name": "BCA", "account_number": "8295032185", "account_name": "Rekening Utama",
                    "account_holder_name": self.holder}
    assert await DV.rekening_tagih(C(None), "g") == "BCA 8295032185"
    assert await DV.rekening_tagih(C("Anthonius Iwan Adhipraja"), "g") == "BCA 8295032185 a.n. Anthonius Iwan Adhipraja"


@pytest.mark.asyncio
async def test_wa_per_faktur_memuat_pemilik_dari_kas_bank():
    class C:
        async def fetch(self, sql, *a):
            if "FROM sales_invoices" in sql:
                assert "tenant_id = $1" in sql
                return [{"id": "f1", "payment_bank_name": "Bank BCA", "payment_account_number": "1111222233",
                         "payment_account_holder": "BCA Operasional"},
                        {"id": "f2", "payment_bank_name": "BCA", "payment_account_number": "4371922746",
                         "payment_account_holder": "BCA Pengeluaran"}]
            assert "FROM bank_accounts" in sql and a == ("kaos",)
            return BARIS
    out = await DV.rekening_per_faktur(C(), "kaos", ["f1", "f2"])
    assert out == {"f1": "Bank BCA 1111222233 a.n. PT Kaos Biru", "f2": "BCA 4371922746"}


def test_skema_kas_bank_membawa_account_holder_name():
    from app.schemas import bank_accounts as S
    for m in (S.CreateBankAccountRequest, S.UpdateBankAccountRequest, S.BankAccountDetail, S.BankAccountListItem):
        assert "account_holder_name" in m.model_fields, m.__name__
    assert S.UpdateBankAccountRequest(account_holder_name="PT X").account_holder_name == "PT X"


@pytest.mark.asyncio
async def test_snapshot_dokumen_tanpa_rekening_tak_menyentuh_bank_accounts():
    c = _Conn(BARIS)
    assert await FC.pemilik_cetak(c, "kaos", "BCA", None, None) is None
    assert await FC.pemilik_cetak(c, "kaos", None, " - ", "  ") is None
    assert c.sql == []
    assert await FC.pemilik_cetak(c, "kaos", "BCA", None, "Sari") == "Sari" and len(c.sql) == 1
