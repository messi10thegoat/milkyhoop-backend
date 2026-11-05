"""BUG-057 (6 Nov 2026): WAC = nilai bersih / qty on-hand (V480), bukan average_cost baris terbaru; pemulih PO saat void juga lewat kepala tagihan.
Gerbang DB dua sisi (pasang -> 0 salah; definisi lama -> 5 salah; pembalik -> definisi identik): scripts/ops/gerbang-bug057-wac.sql."""
import asyncio
import os
import re
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.services import po_daftar as PD  # noqa: E402

MIG = Path(__file__).resolve().parents[3] / "migrations"


def _baca(n):
    return (MIG / n).read_text()


def test_v480_rumus_nilai_per_qty_dan_cadangan_lama():
    s = _baca("V480__wac_dari_nilai_per_qty.sql")
    assert "SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost)" in s and "ROUND(v_nilai / v_qty, 2)" in s
    assert "IF v_qty > 0 AND v_nilai >= 0 THEN" in s
    assert s.index("ROUND(v_nilai / v_qty, 2)") < s.index("ORDER BY movement_date DESC, created_at DESC")  # nilai/qty DULU, baris terakhir cadangan
    assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", s, re.M)  # apply_mig.sh -1 menolak kendali transaksi


def test_v480_pembalik_ada_dan_tanpa_rumus_baru():
    s = _baca("V480__wac_dari_nilai_per_qty_ROLLBACK.sql")
    assert "CREATE OR REPLACE FUNCTION public.get_weighted_average_cost" in s and "v_nilai" not in s
    assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", s, re.M)


class _K:
    def __init__(self):
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return [{"po_id": "po-1"}]


def test_po_terkait_bill_juga_lewat_kepala_tagihan_bertenant():
    k = _K()
    assert asyncio.run(PD.po_terkait_bill(k, "t", "bill-1")) == ["po-1"]
    sql, a = k.sql[0]
    assert "bi.purchase_order_item_id" in sql and "b.purchase_order_id" in sql and "UNION" in sql
    assert "b.tenant_id = $2" in sql and sql.count("po.tenant_id = $2") == 2 and a == ("bill-1", "t")  # Law 24
