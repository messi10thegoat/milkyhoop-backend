"""#43 — respons VOID faktur dan SISA di detail faktur void.

(a) POST /sales-invoices/{id}/void membalas `data.status` = literal "draft"
    untuk faktur yang baru di-void (sejak 38f05ec8e). Kini dibaca dari
    `UPDATE sales_invoices ... RETURNING status`.
(b) GET /sales-invoices/{id} untuk faktur VOID melaporkan
    remaining_amount = total (kaos INV-2609-0077: 850.000), padahal DAFTAR
    sudah 0. Kini void -> 0, draf tetap = total.

Kontrak (a) dan penyambungan (b) diperiksa lewat AST berkas router: tes
helper murni saja TIDAK menjaga penyambungan (helper benar, handler tak
memanggilnya = tetap hijau).
"""

import ast
from decimal import Decimal

import app.routers.sales_invoices as si
from app.routers.sales_invoices import _t43_dibayar_dan_sisa


# ------------------------------------------------------------ (b) helper murni
def test_void_tanpa_baris_piutang_sisa_nol():
    assert _t43_dibayar_dan_sisa("void", Decimal("850000"), None) == (0.0, 0.0)


def test_draf_tanpa_baris_piutang_sisa_sama_dengan_total():
    assert _t43_dibayar_dan_sisa("draft", Decimal("850000"), None) == (0.0, 850000.0)


def test_terbit_tanpa_baris_piutang_berarti_lunas():
    # lunas (termasuk lewat nota kredit/retur): compute_ar_outstanding tak memberi baris
    assert _t43_dibayar_dan_sisa("posted", Decimal("170000"), None) == (170000.0, 0.0)


def test_dengan_baris_piutang_memakai_outstanding_buku():
    assert _t43_dibayar_dan_sisa(
        "partial", Decimal("4290000"), Decimal("1825000")
    ) == (2465000.0, 1825000.0)


def test_dengan_baris_piutang_bersen_dihitung_decimal():
    dibayar, sisa = _t43_dibayar_dan_sisa(
        "partial", Decimal("1000000.10"), Decimal("0.20")
    )
    assert dibayar == float(Decimal("999999.90"))
    assert sisa == 0.2


# ------------------------------------------------------------ AST berkas router
def _fungsi(nama):
    tree = ast.parse(open(si.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == nama:
            return node
    raise AssertionError(f"fungsi {nama} tak ditemukan")


def test_detail_memanggil_helper_sisa():
    fn = _fungsi("get_invoice")
    dipanggil = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_t43_dibayar_dan_sisa"
    ]
    assert len(dipanggil) == 1, "get_invoice harus menghitung dibayar/sisa lewat helper #43"
    # tak boleh ada cabang lama yang menyamakan draf dan void
    for n in ast.walk(fn):
        if isinstance(n, ast.Tuple) and {
            getattr(e, "value", None) for e in n.elts
        } == {"draft", "void"}:
            raise AssertionError('cabang ("draft","void") -> total masih ada di get_invoice')


def _nilai_status_respons(fn):
    """Nilai `data.status` pada return yang pesannya 'voided successfully'."""
    for n in ast.walk(fn):
        if not isinstance(n, ast.Dict):
            continue
        kunci = [getattr(k, "value", None) for k in n.keys]
        if "message" not in kunci or "data" not in kunci:
            continue
        msg = n.values[kunci.index("message")]
        if "voided" not in str(getattr(msg, "value", "")):
            continue
        data = n.values[kunci.index("data")]
        dk = [getattr(k, "value", None) for k in data.keys]
        return data.values[dk.index("status")]
    raise AssertionError("return sukses void tak ditemukan")


def test_void_membalas_status_dari_returning():
    fn = _fungsi("void_invoice")
    nilai = _nilai_status_respons(fn)
    assert isinstance(nilai, ast.Name), (
        f"data.status void harus dari DB, bukan literal {ast.dump(nilai)}"
    )
    # variabel itu harus hasil fetchval atas UPDATE sales_invoices ... RETURNING status
    sumber = None
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) == nilai.id for t in n.targets)
            and isinstance(n.value, ast.Await)
            and isinstance(n.value.value, ast.Call)
            and getattr(n.value.value.func, "attr", None) == "fetchval"
        ):
            sql = n.value.value.args[0]
            sumber = getattr(sql, "value", "")
    assert sumber, f"{nilai.id} tidak berasal dari conn.fetchval(...)"
    s = " ".join(sumber.split())
    assert "UPDATE sales_invoices" in s and "status = 'void'" in s, s
    assert "RETURNING status" in s, s
