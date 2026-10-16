"""T201 — panel agregat per Sales Order (nilai · ditagih · diterima · sisa).

Setiap tes di berkas ini DIBUKTIKAN BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import re
from pathlib import Path

from app.routers import proformas as pf

ROUTER_SRC = Path(pf.__file__).read_text()


def _kode_saja(src: str) -> str:
    """Buang docstring dan komentar — kita menguji KODE, bukan prosa."""
    tanpa_doc = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(
        b for b in tanpa_doc.splitlines() if not b.strip().startswith("#")
    )


ROUTER_KODE = _kode_saja(ROUTER_SRC)


import pytest

from app.services import uang_muka_pesanan as ump

# KONTRAK-dp-neto (16 Okt 2026): deposit_totals_for_order = pembungkus services/uang_muka_pesanan (jurnal, neto refund).
# Baris jurnal per uang muka dipalsukan; sifat SQL diuji pada ump.SQL_PER_UANG_MUKA (satu sumber).
_SQL = " ".join(ump.SQL_PER_UANG_MUKA.split())


def _dp(neto, proforma=None, status="posted", refund=0):
    return {"id": f"dp-{neto}-{proforma}", "so_id": "so-1", "status": status, "amount": neto + refund,
            "proforma_id": proforma, "terima": ump._d(neto + refund), "terap": ump.NOL, "lain": ump._d(refund),
            "neto": ump._d(neto)}


@pytest.fixture
def palsu(monkeypatch):
    catat = {}

    def pasang(baris):
        async def f(conn, tenant_id, so_ids):
            catat["arg"] = (tenant_id, list(so_ids))
            return baris
        monkeypatch.setattr(ump, "per_uang_muka", f)
        return catat
    return pasang


# ------------------------------------------------------- received_total


async def test_received_total_mengecualikan_void(palsu):
    """`void` HARUS disaring di SQL; draf (janji, tanpa jurnal) bukan uang diterima."""
    assert "customer_deposits" in _SQL
    assert "cd.status <> 'void'" in _SQL, _SQL
    palsu([_dp(162000), _dp(0, status="draft")])
    hasil = await pf.deposit_totals_for_order(None, "kaos-biru-konveksi", "so-1")
    assert hasil["received_total"] == 162000.0


async def test_received_total_neto_setelah_refund(palsu):
    """KONTRAK-dp-neto: refund mengurangi uang yang diterima pesanan (NetSuite: deposit − refund)."""
    palsu([_dp(40000, refund=10000)])
    assert (await pf.deposit_totals_for_order(None, "t", "so-1"))["received_total"] == 40000.0


async def test_received_total_float_bukan_decimal_atau_string(palsu):
    """pydantic v2 menyerialkan Decimal jadi STRING → merusak matematika FE."""
    palsu([_dp(162000)])
    hasil = await pf.deposit_totals_for_order(None, "t", "so-1")
    assert type(hasil["received_total"]) is float, type(hasil["received_total"])
    assert type(hasil["unbilled_received"]) is float
    assert hasil["received_total"] == 162000.0


async def test_received_total_nol_saat_tidak_ada_deposit(palsu):
    """Nol, bukan None — kontrol negatif tidak boleh mengirim null ke FE."""
    palsu([])
    hasil = await pf.deposit_totals_for_order(None, "t", "so-1")
    assert hasil == {"received_total": 0.0, "unbilled_received": 0.0}


# ---------------------------------------------------- unbilled_received


async def test_unbilled_hanya_deposit_tanpa_proforma_id(palsu):
    """`unbilled_received` = uang muka TANPA proforma_id saja, bukan seluruh jumlah."""
    palsu([_dp(100000), _dp(1500000, proforma="pf-1")])
    hasil = await pf.deposit_totals_for_order(None, "t", "so-1")
    assert hasil["unbilled_received"] == 100000.0


async def test_unbilled_terpisah_dari_received(palsu):
    """Dua angka berbeda: sebagian bertaut proforma, sebagian tidak."""
    palsu([_dp(100000), _dp(1500000, proforma="pf-1")])
    hasil = await pf.deposit_totals_for_order(None, "t", "so-1")
    assert hasil["received_total"] == 1600000.0
    assert hasil["unbilled_received"] == 100000.0


# ------------------------------------------------------------- ISOLASI


async def test_query_menyaring_tenant_id(palsu):
    """Gateway BYPASSRLS — RLS TIDAK melindungi jalur ini. tenant_id wajib."""
    assert "cd.tenant_id = $1" in _SQL, f"tenant_id tidak disaring. SQL:\n{_SQL}"
    c = palsu([])
    await pf.deposit_totals_for_order(None, "kaos-biru-konveksi", "so-1")
    assert c["arg"] == ("kaos-biru-konveksi", ["so-1"])


async def test_atribusi_lewat_sales_order_id_bukan_tanggal():
    """Atribusi lewat kunci relasi. Tanggal TIDAK BOLEH muncul di penyaring (created_at hanya DIPILIH untuk urutan
    pencocokan proforma_atribusi, bukan untuk atribusi)."""
    where = _SQL.lower().split(" where cd.tenant_id", 1)[1].split(" group by ", 1)[0]
    assert "cd.sales_order_id = any($2::uuid[])" in where and "p.sales_order_id = any($2::uuid[])" in where
    for kata in ("deposit_date", "created_at", "date_trunc", "between", "::date"):
        assert kata not in where, (
            f"query agregat memakai tanggal ('{kata}') — atribusi harus lewat "
            f"sales_order_id/proforma_id. SQL:\n{_SQL}"
        )


# ------------------------------------------------- respons endpoint SO


async def test_respons_endpoint_membawa_kelima_agregat():
    """Panel butuh kelimanya di AKAR respons daftar (bukan di `.data`)."""
    blok = ROUTER_KODE.split("async def list_proformas_for_order")[1]
    for kunci in (
        '"order_total"',
        '"issued_total"',
        '"billable_remaining"',
        '"received_total"',
        '"unbilled_received"',
    ):
        assert kunci in blok, f"{kunci} tidak dikembalikan endpoint SO-proformas"


async def test_endpoint_memanggil_helper_diterima():
    """Angka DIHITUNG saat baca — bukan dibaca dari kolom ringkasan."""
    blok = ROUTER_KODE.split("async def list_proformas_for_order")[1]
    assert "deposit_totals_for_order(conn," in blok


async def test_tak_ada_kolom_ringkasan_disimpan():
    """Nol kolom ringkasan: tak ada UPDATE ke kolom agregat mana pun."""
    for terlarang in (
        "SET received_total",
        "SET unbilled_received",
        "received_total =",
        "unbilled_received =",
    ):
        assert terlarang not in ROUTER_KODE, (
            f"'{terlarang}' — agregat mulai DISIMPAN, bukan dihitung"
        )
