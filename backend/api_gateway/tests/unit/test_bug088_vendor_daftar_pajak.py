"""BUG-088 (BE aditif): GET /api/vendors (daftar) membawa is_pkp + default_tax_code (== GET /{id}) supaya form PO/FPB CW bisa memasang
pajak bawaan vendor tanpa panggilan tambahan. Penjaga pola BUG-097: SETIAP kunci literal yang dirakit list_vendors untuk satu baris
WAJIB dideklarasikan VendorListItem (medan tak dideklarasikan DIBUANG DIAM-DIAM oleh response_model); ketiga SELECT daftar memilihnya."""
import ast
import pathlib
import re

from app.schemas import vendors as S

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "vendors.py"


def _fungsi():
    pohon = ast.parse(RUTE.read_text())
    return next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "list_vendors")


def test_semua_kunci_baris_daftar_vendor_dideklarasikan():
    kamus = [[k.value for k in n.keys if isinstance(k, ast.Constant)] for n in ast.walk(_fungsi()) if isinstance(n, ast.Dict)]
    baris = [ks for ks in kamus if "payment_terms_days" in ks]
    assert baris and any("default_tax_code" in ks and "is_pkp" in ks for ks in baris)  # kontrol: penjaga menemukan kamus baris
    for ks in baris:
        assert [k for k in ks if k not in S.VendorListItem.model_fields] == []


def test_semua_select_daftar_memilih_medan_pajak():
    src = ast.get_source_segment(RUTE.read_text(), _fungsi())
    pilih = [m for m in re.findall(r"SELECT (.*?) FROM vendors", src, re.S) if "payment_terms_days" in m]
    assert len(pilih) == 3 and all("is_pkp" in m and "default_tax_code" in m for m in pilih)


def test_skema_menyampaikan_medan_pajak():
    x = S.VendorListItem.model_validate({"id": "v1", "name": "PT A", "payment_terms_days": 30, "is_active": True, "created_at": "x",
                                         "is_pkp": True, "default_tax_code": "PPN_11"}).model_dump()
    assert (x["is_pkp"], x["default_tax_code"]) == (True, "PPN_11")
