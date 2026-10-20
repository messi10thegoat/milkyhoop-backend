"""Data daftar/detail UANG MUKA seragam dengan Pesanan/Faktur (S2-UM, 20 Okt 2026, MASTER). Server menghitung, FE memformat.

  sisa              = jurnal (routers/customer_deposits.compute_deposit_remaining_many / SQL_SISA_UANG_MUKA), BUKAN cache.
                      Untuk filter & ringkasan (semua uang muka tenant sekaligus) teks SQL DITURUNKAN dari konstanta yang sama
                      (sql_sisa_semua) -- digerbang per uang muka == compute_deposit_remaining_many.
  aksi              = 'belum_diterapkan' : terbit (bukan draf/void) DAN sisa jurnal > 0 (pill + ?aksi= + summary: predikat SAMA)
  sisa_keadaan      = draf | utuh | sebagian | terpakai | dikembalikan | sebagian_dikembalikan | batal
                      (dari status_detail yang sudah ada; konsistensinya dengan sisa jurnal digerbang harness)
  status_text       = label layar teks_galat.status_id("dp", status_detail) == peta FE r288 (statusUangMuka.ts)
`remaining_state` lama ("posted" | "draft_belum_diposting") TIDAK diubah -- FE live membacanya (sisaUangMuka.ts).
"""
from decimal import Decimal
from typing import Optional

AKSI_BELUM_DITERAPKAN = "belum_diterapkan"
AKSI_UM = (AKSI_BELUM_DITERAPKAN,)
KEADAAN_UM = ("draf", "utuh", "sebagian", "terpakai", "dikembalikan", "sebagian_dikembalikan", "batal")
_KEADAAN_DARI_DETAIL = {"draft": "draf", "posted": "utuh", "partial": "sebagian", "applied": "terpakai",
                        "refunded": "dikembalikan", "partially_refunded": "sebagian_dikembalikan", "void": "batal"}
CTE_SISA = "sisa_um"


def sql_sisa_semua(p_akun: str) -> str:
    """Sisa jurnal SEMUA uang muka bukan-draf tenant $1 -> (id, remaining). Diturunkan TEKS dari SQL_SISA_UANG_MUKA (satu sumber):
    daftar id `unnest($2)` diganti uang muka tenant, `$3` (akun peran CUSTOMER_DEPOSIT_LIABILITY) diganti `p_akun`."""
    from ..routers.customer_deposits import SQL_SISA_UANG_MUKA as S
    asal = "unnest($2::uuid[]) AS u(x)"
    assert S.count(asal) == 1 and S.count("ANY($2::uuid[])") == 3 and S.count("$3") == 1
    s = S.replace(asal, "(SELECT cd0.id FROM customer_deposits cd0 WHERE cd0.tenant_id = $1 AND cd0.status <> 'draft') AS u(x)")
    s = s.replace("ANY($2::uuid[])", "ANY(ARRAY(SELECT id FROM d))")
    return s.replace("$3", p_akun)


def sql_sisa_baris(alias: str = "customer_deposits") -> str:
    """Sisa jurnal satu baris daftar (CTE sisa_um harus ada di kueri). Draf = NULL."""
    return f"(SELECT s.remaining FROM {CTE_SISA} s WHERE s.id = {alias}.id)"


def sql_aksi(aksi: str, alias: str = "customer_deposits") -> str:
    """Predikat SQL kunci aksi; kembaran Python: aksi_um()."""
    if aksi != AKSI_BELUM_DITERAPKAN:
        raise ValueError(aksi)
    return f"({alias}.status NOT IN ('draft', 'void') AND COALESCE({sql_sisa_baris(alias)}, 0) > 0)"


def aksi_um(status: str, sisa) -> Optional[str]:
    if status in ("draft", "void") or sisa is None:
        return None
    return AKSI_BELUM_DITERAPKAN if Decimal(str(sisa)) > 0 else None


def keadaan_um(status_detail: str) -> str:
    return _KEADAAN_DARI_DETAIL.get(status_detail, status_detail)


def status_text_um(status_detail: str) -> str:
    from . import teks_galat as tg
    return tg.status_id("dp", status_detail)


def keadaan_cocok_sisa(keadaan: str, amount, sisa) -> bool:
    """Gerbang (harness): keadaan dari status_detail konsisten dengan sisa JURNAL."""
    if keadaan in ("draf",):
        return sisa is None
    s, a = Decimal(str(sisa or 0)), Decimal(str(amount or 0))
    if keadaan == "batal":
        return s == 0
    if keadaan == "utuh":
        return s == a and a > 0
    if keadaan in ("terpakai", "dikembalikan"):
        return s == 0
    return 0 < s < a  # sebagian / sebagian_dikembalikan
