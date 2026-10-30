"""FPB-DEBIT-SATU (30 Okt 2026, aturan disetujui LANGSUNG pemilik): akun debit baris FPB TANPA produk = COGS_SALES di KEDUA jalur posting
(draf->posting lewat tagihan_aksi.hitung_debit; buat-langsung-posted lewat bills_service.create_bill_v2). Dulu jalur buat-langsung mendebit
INVENTORY_MERCHANDISE (Persediaan tanpa baris stok). Penentu SATU: tagihan_aksi.akun_beban_default. Bukti di DB nyata (kedua jalur,
debit per akun identik; kontrol merah di pohon dasar): scripts/ops/uji-nyata-fpb-debit.py."""
import asyncio
import inspect
import textwrap
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import bills_service as BS
from app.services import tagihan_aksi as TA

AKUN = {"COGS_SALES": uuid4(), "COGS_SERVICE": uuid4()}


class _K:
    def __init__(s, peran=None, rows=()):
        s.peran = AKUN if peran is None else peran
        s.rows, s.sql = list(rows), []

    async def fetchval(s, sql, *a):
        s.sql.append((sql, a))
        if "FROM account_roles" in sql:
            return s.peran.get(a[1])
        return None   # tagihan_pembuka -> bukan pembuka

    async def fetch(s, sql, *a):
        return s.rows


@pytest.mark.parametrize("item_type,peran", [(None, "COGS_SALES"), ("goods", "COGS_SALES"), ("service", "COGS_SERVICE")])
def test_akun_beban_default_per_jenis(item_type, peran):
    k = _K()
    assert asyncio.run(TA.akun_beban_default(k, "t", item_type)) == AKUN[peran]
    assert k.sql[-1][1] == ("t", peran)


def test_akun_beban_default_tak_terpetakan_422():
    with pytest.raises(HTTPException) as e:
        asyncio.run(TA.akun_beban_default(_K(peran={}), "t", None))
    assert e.value.status_code == 422 and e.value.detail["code"] == "ACCOUNT_DEFAULT_UNMAPPED"


def test_jalur_draf_baris_tanpa_produk_ke_cogs_sales():
    rows = [{"id": uuid4(), "product_id": None, "net": Decimal("30021"), "item_type": None, "track_inventory": None, "purchase_account_id": None}]
    out = asyncio.run(TA.hitung_debit(_K(rows=rows), "t", uuid4(), Decimal("30021"), uuid4()))
    assert out == [(AKUN["COGS_SALES"], Decimal("30021"), False)]


def test_jalur_buat_langsung_baris_tanpa_produk_lewat_penentu_satu():
    # FPB-DEBIT-PENUH (30 Okt 2026): jalur buat-langsung kini memanggil TA.hitung_debit SELURUHNYA (tak ada lagi cabang `pid is None`
    # sendiri); baris tanpa produk di hitung_debit -> akun_beban_default(item_type None) -> COGS_SALES (bukan Persediaan, is_inv False).
    src = textwrap.dedent(inspect.getsource(BS.BillsService.create_bill_v2))
    assert "await TA.hitung_debit(conn, tenant_id, bill_id, subtotal_dec, debit_acct_id)" in src
    assert "_accumulate(debit_acct_id, net_dec, True)" not in src   # cabang lama Persediaan tak kembali
    rows = [{"id": uuid4(), "product_id": None, "net": Decimal("7"), "item_type": None, "track_inventory": None, "purchase_account_id": None}]
    assert asyncio.run(TA.hitung_debit(_K(rows=rows), "t", uuid4(), Decimal("7"), uuid4())) == [(AKUN["COGS_SALES"], Decimal("7"), False)]


def test_hitung_debit_memakai_penentu_yang_sama():
    src = inspect.getsource(TA.hitung_debit)
    assert "await akun_beban_default(conn, tid, dr[\"item_type\"])" in src
    assert "FROM account_roles" not in src   # tak ada salinan kueri peran di hitung_debit
