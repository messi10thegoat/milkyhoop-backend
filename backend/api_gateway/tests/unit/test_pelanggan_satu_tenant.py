"""Pelanggan WAJIB milik tenant yang sama di SETIAP jalur tulis yang menerima customer_id (30 Sep 2026, MASTER).

Dulu SO/faktur/penawaran (buat+ubah), ubah penerimaan, ubah uang muka, cek masuk, nota penjualan tunai dan perintah
produksi hanya memeriksa FORMAT UUID -> id pelanggan tenant lain bisa tersimpan (diukur 30 Sep: 0 baris lintas tenant
di 15 tabel; lubang, belum dipakai). Pemeriksaan = pihak_helpers.pelanggan_kanonik_tenant (SQL dengan tenant_id
eksplisit, bukan set_config), dipanggil SEBELUM baris dokumen ditulis. Perilaku nyata diuji di salinan DB.
"""
import ast
import os
from pathlib import Path
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import pihak_helpers as PH  # noqa: E402

R = Path(PH.__file__).resolve().parents[1] / "routers"

# (berkas, fungsi, SQL tulis dokumen yang harus DIDAHULUI pemeriksaan) -- literal, bukan dibaca dari kode
JALUR = [
    ("sales_orders.py", "create_sales_order", "INSERT INTO sales_orders"),
    ("sales_orders.py", "update_sales_order", "UPDATE sales_orders"),
    ("sales_invoices.py", "create_invoice", "INSERT INTO sales_invoices"),
    ("sales_invoices.py", "update_invoice", "UPDATE sales_invoices SET"),
    ("quotes.py", "create_quote", "INSERT INTO quotes"),
    ("quotes.py", "update_quote", "UPDATE quotes"),
    ("receive_payments.py", "update_receive_payment", "UPDATE receive_payments"),
    ("customer_deposits.py", "create_customer_deposit", "INSERT INTO customer_deposits"),
    ("customer_deposits.py", "update_customer_deposit", "UPDATE customer_deposits"),
    ("cheques.py", "receive_cheque", "INSERT INTO cheques"),
    ("sales_receipts.py", "create_sales_receipt", "INSERT INTO sales_receipts"),
    ("production.py", "create_production_order", "INSERT INTO production_orders"),
    ("credit_notes.py", "buat_nota_kredit", "INSERT INTO credit_notes"),
    # kode lama (13 Sep): hitung-ulang total ditulis dulu, pelanggan diperiksa sesudahnya DI TRANSAKSI YANG SAMA
    # (400 -> seluruh tulisan batal). Diterima HANYA bila keduanya di dalam satu async with conn.transaction().
    ("credit_notes.py", "update_credit_note", "UPDATE credit_notes", "tx"),
]


def _fungsi(berkas, nama):
    src = (R / berkas).read_text(encoding="utf-8")
    fn = [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nama]
    assert len(fn) == 1, (berkas, nama)
    return fn[0], src


@pytest.mark.parametrize("berkas,nama,tulis,mode", [j if len(j) == 4 else (*j, "sebelum") for j in JALUR])
def test_pemeriksaan_satu_tenant_sebelum_tulis(berkas, nama, tulis, mode):
    fn, src = _fungsi(berkas, nama)
    cek = [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "pelanggan_kanonik_tenant"]
    assert cek, f"{berkas}:{nama} menulis customer_id tanpa pelanggan_kanonik_tenant"
    baris_tulis = [n.lineno for n in ast.walk(fn) if isinstance(n, (ast.Constant, ast.JoinedStr))
                   and tulis in " ".join((ast.get_source_segment(src, n) or "").split())]
    assert baris_tulis, f"{berkas}:{nama}: SQL '{tulis}' tak ditemukan (pola uji basi?)"
    if mode == "tx":
        tx = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith) and "conn.transaction" in ast.unparse(n.items[0].context_expr)]
        dalam = lambda ln: any(t.lineno <= ln <= t.end_lineno for t in tx)  # noqa: E731
        assert tx and all(dalam(x) for x in baris_tulis) and all(dalam(x) for x in cek), f"{berkas}:{nama}: tak satu transaksi"
        return
    assert min(cek) < min(baris_tulis), f"{berkas}:{nama}: pemeriksaan pelanggan SESUDAH '{tulis}'"


class _C:
    def __init__(self, milik):
        self.milik, self.q = milik, []

    async def fetchval(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return 1 if (str(a[0]), a[1]) in self.milik else None


T, LAIN = "kaos-biru-konveksi", "grapgrap-manado"
P = UUID("40000000-0000-0000-0000-0000000000c1")


@pytest.mark.asyncio
async def test_pelanggan_tenant_lain_ditolak_dengan_pesan_sama_seperti_tak_ada():
    c = _C({(str(P), LAIN)})
    with pytest.raises(HTTPException) as e:
        await PH.pelanggan_kanonik_tenant(c, T, str(P))
    assert (e.value.status_code, e.value.detail) == (400, "Pelanggan tidak ditemukan")
    assert c.q[0] == ("SELECT 1 FROM customers WHERE id = $1 AND tenant_id = $2", (P, T))  # tenant EKSPLISIT
    with pytest.raises(HTTPException) as e2:
        await PH.pelanggan_kanonik_tenant(_C(set()), T, str(P))
    assert e2.value.detail == e.value.detail  # tak membocorkan keberadaan lintas tenant


@pytest.mark.asyncio
async def test_pelanggan_sendiri_lolos_kanonik_dan_kosong_tetap_kosong():
    assert await PH.pelanggan_kanonik_tenant(_C({(str(P), T)}), T, str(P).upper()) == str(P)
    assert await PH.pelanggan_kanonik_tenant(_C(set()), T, None) is None
    with pytest.raises(HTTPException) as e:
        await PH.pelanggan_kanonik_tenant(_C(set()), T, "Toko Melati")
    assert e.value.status_code == 400
