"""Backlog 3b (26 Sep 2026): production_costing tanpa `SET app.tenant_id = '<f-string>'`.

Terukur: 8 handler menjalankan SET sesi dengan tenant_id DIINTERPOLASI ke SQL (f-string). GUC itu tak dibaca
apa pun di jalur ini — peran aplikasi `postgres` = superuser + BYPASSRLS (205 kebijakan RLS tak berlaku),
fungsi yang membaca app.tenant_id hanya verify_* (tak dipanggil di sini). Jadi SET = penjaga ilusi +
permukaan injeksi. Pagar nyata = filter tenant_id eksplisit di setiap kueri (UPDATE cost_pools kini ikut
berpagar tenant). Dihapus, BUKAN diganti set_config lepas (yang di luar transaksi pun NO-OP; G3).
"""
import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "app/routers/production_costing.py").read_text()


def test_tak_ada_set_guc_fstring():
    tree = ast.parse(SRC)
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            teks = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
            assert "app.tenant_id" not in teks, "SET app.tenant_id lewat f-string masih ada"
    assert "SET app.tenant_id" not in SRC


def test_update_cost_pools_berpagar_tenant():
    s = " ".join(SRC.split())
    assert "UPDATE cost_pools SET actual_amount = $1, updated_at = NOW() WHERE id = $2 AND tenant_id = $3" in s


def test_setiap_update_delete_berpagar_tenant():
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            s = " ".join(n.value.split())
            if s.upper().startswith(("UPDATE ", "DELETE ")):
                assert "tenant_id" in s, s[:100]
