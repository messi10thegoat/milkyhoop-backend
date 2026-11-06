"""TEKS-JURNAL-NKND (3 Nov 2026, OK MASTER; tiket CONVERSATIONAL): deskripsi jurnal Nota Kredit / Nota Debit berbahasa Indonesia mengikuti
pola gold Penjualan "{Dokumen} {nomor} - {pihak}", dari SATU sumber (services/teks_jurnal) yang dipakai penulis DAN pratinjau.
Kesetaraan SQL penulis (selain teks) = SETARA perekam; tes ini mengunci teks + bahwa semua pintu memanggil fungsi yang sama."""
import ast
import inspect

import pytest

from app.routers import credit_notes as RCN
from app.routers import vendor_credits as RVC
from app.services import inventory_helpers as IH
from app.services import nota_debit as ND
from app.services import nota_debit_rencana as NDR
from app.services import nota_kredit_rencana as NKR
from app.services import teks_jurnal as TJ


def test_teks_persis():
    assert TJ.nk("CN-1", "Toko A") == "Nota Kredit CN-1 - Toko A"
    assert TJ.nk_hpp("CN-1") == "Pembalik HPP CN-1"
    assert TJ.nk_refund("CN-1", "Toko A") == "Pengembalian Dana Nota Kredit CN-1 - Toko A"
    assert TJ.nk_batal("CN-1", "Toko A") == "Batal Nota Kredit CN-1 - Toko A"
    assert TJ.nk_batal_hpp("CN-1") == "Batal Pembalik HPP CN-1"
    assert TJ.nd("VC-1", "PT B") == "Nota Debit VC-1 - PT B"
    assert TJ.nd_refund("VC-1", "PT B") == "Penerimaan Dana Nota Debit VC-1 - PT B"
    assert TJ.nd_batal("VC-1", "PT B") == "Batal Nota Debit VC-1 - PT B"
    assert TJ.nd_batal_hpp("VC-1") == "Batal HPP VC-1"


def test_nd_tanpa_jurnal_hpp_baru():
    # BUG-053: retur barang dibukukan di jurnal posting ND (perpetual) -> tak ada lagi teks HPP ND baru; batal HPP tetap untuk ND lama
    assert not hasattr(TJ, "nd_hpp") and hasattr(TJ, "nd_batal_hpp")


def _panggilan_tj(obj):
    src = inspect.getsource(obj)
    pohon = ast.parse(src if not src.startswith((" ", "\t")) else "if 1:\n" + src)
    return sorted({n.func.attr for n in ast.walk(pohon) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and getattr(n.func.value, "id", None) == "TJ"})


@pytest.mark.parametrize("obj,wajib", [
    (RCN, ["nk", "nk_batal", "nk_batal_hpp", "nk_hpp", "nk_refund"]),     # penulis NK
    (NKR, ["nk", "nk_batal", "nk_batal_hpp", "nk_hpp", "nk_refund"]),     # pratinjau NK
    (RVC, ["nd", "nd_batal", "nd_refund"]),                               # penulis ND
    (ND, ["nd_batal_hpp"]),                                               # penulis batal HPP ND
    (NDR, ["nd", "nd_batal", "nd_batal_hpp", "nd_refund"]),               # pratinjau ND (BUG-053: tanpa nd_hpp)
])
def test_penulis_dan_pratinjau_memanggil_satu_sumber(obj, wajib):
    assert all(w in _panggilan_tj(obj) for w in wajib), (obj.__name__, _panggilan_tj(obj))


LAMA = ("Credit Note ", "COGS reversal ", "Vendor Credit ", "Void COGS companion", "Void HPP nota debit", "Refund Received ")


@pytest.mark.parametrize("obj", [RCN, NKR, RVC, ND, NDR])
def test_tak_ada_fstring_deskripsi_lama(obj):
    """AST: tak ada f-string berawalan teks lama; 'Void '/'Refund ' + nomor NK/ND juga tak ada (docstring/komentar tak ikut)."""
    liar = []
    for n in ast.walk(ast.parse(inspect.getsource(obj))):
        if isinstance(n, ast.JoinedStr) and n.values and isinstance(n.values[0], ast.Constant):
            awal = n.values[0].value
            nomor = any(isinstance(v, ast.FormattedValue) and "credit_n" in ast.unparse(v.value) for v in n.values)
            if awal.startswith(LAMA) or (awal in ("Void ", "Refund ") and nomor):
                liar.append((n.lineno, ast.unparse(n)[:80]))
    assert liar == [], liar
