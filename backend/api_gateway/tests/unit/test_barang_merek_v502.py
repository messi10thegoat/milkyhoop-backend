"""BUG-260 / V502 (16 Nov 2026): kolom MEREK barang -- products.merek (teks, nullable, tanpa backfill), isian di POST/PATCH /api/items,
GET /api/items?merek=, GET /api/items/merek (daftar nilai), Impor/Ekspor Barang. Migrasi ADITIF; apply = MASTER sesudah gerbang LULUS.
URUTAN RILIS: V502 DULU, baru kode (INSERT/UPDATE items membaca kolom merek)."""
import pathlib
import re

from app.schemas import items as S
from app.services import impor_barang as IB

BE = pathlib.Path(__file__).resolve().parents[3]
RUTE = (BE / "api_gateway" / "app" / "routers" / "items.py").read_text()
MIG = (BE / "migrations" / "V502__products_merek.sql").read_text()
ROLL = (BE / "migrations" / "V502__products_merek_ROLLBACK.sql").read_text()


def test_migrasi_aditif_tanpa_transaksi_dan_ada_pembalik():
    kode = "\n".join(b for b in MIG.splitlines() if not b.lstrip().startswith("--"))
    assert "ALTER TABLE products ADD COLUMN IF NOT EXISTS merek TEXT;" in kode      # nullable, tanpa default/backfill
    alter = next(b for b in kode.splitlines() if b.startswith("ALTER TABLE"))
    assert "NOT NULL" not in alter and "DEFAULT" not in alter and "UPDATE " not in kode.upper()   # nullable, tanpa default, tanpa backfill
    assert "CREATE INDEX IF NOT EXISTS idx_products_tenant_merek ON products (tenant_id, merek)" in kode
    for teks in (MIG, ROLL):
        assert not re.search(r"^\s*(BEGIN|COMMIT)\b", teks, re.M | re.I)             # runner membungkus sendiri
    assert "DROP COLUMN IF EXISTS merek" in ROLL and "DROP INDEX IF EXISTS idx_products_tenant_merek" in ROLL


def test_skema_merek_dipangkas_kosong_jadi_none():
    kw = dict(name="Aqua 600", base_unit="pcs")
    assert S.CreateItemRequest(**kw, merek="  Aqua ").merek == "Aqua"
    assert S.CreateItemRequest(**kw, merek="   ").merek is None
    assert S.CreateItemRequest(**kw).merek is None
    assert S.UpdateItemRequest(merek=" Indomie ").model_dump(exclude_unset=True) == {"merek": "Indomie"}
    assert S.UpdateItemRequest(merek="").model_dump(exclude_unset=True) == {"merek": None}   # kosong = hapus merek
    assert "merek" not in S.UpdateItemRequest(name="x").model_dump(exclude_unset=True)          # tak dikirim = tak disentuh
    assert "merek" in S.ItemListItem.model_fields


def test_insert_buat_barang_kolom_dan_placeholder_sama_banyak():
    m = re.search(r"insert_query = \"\"\"\s*INSERT INTO products \((.*?)\)\s*VALUES \((.*?)\)\s*RETURNING id", RUTE, re.S)
    kolom = [k.strip() for k in m.group(1).replace("\n", " ").split(",") if k.strip()]
    nilai = [v.strip() for v in re.sub(r"NOW\(\)", "", m.group(2)).replace("\n", " ").split(",") if v.strip()]
    assert "merek" in kolom and len(kolom) == len(nilai) + 2   # created_at, updated_at = NOW()
    assert max(int(v[1:]) for v in nilai) == len(nilai) == 40


def test_rute_merek_terpasang_dan_urutan_rute():
    assert '        if isinstance(merek, str) and merek.strip():' in RUTE and '            query_parts.append(f"AND p.merek = ${param_idx}")\n            params.append(merek.strip())' in RUTE  # saringan terpasang UTUH
    assert RUTE.index('@router.get("/items/merek")') < RUTE.index('@router.get("/items/{item_id}", response_model=ItemDetailResponse)')
    assert '"merek": ("Merek", "merek")' in RUTE and '"merek": "merek"' in RUTE
    assert '                        "Category",\n                        "Merek",' in RUTE and 'row["merek"],\n                            row["description"]' in RUTE   # header + baris ekspor
    assert 'item_row["reorder_level"]' in RUTE and 'kode_baru,\n                item_row.get("merek"),' in RUTE   # duplikat menyalin merek
    assert "merek IS NOT NULL" in RUTE and "deleted_at IS NULL" in RUTE.split('@router.get("/items/merek")')[1].split("@router.post")[0]


def test_impor_merek_kolom_dan_sinonim_unik():
    assert "merek" in {k for k, _, _ in IB.KOLOM}
    assert IB.SINONIM["merek"] == ["merek", "merk", "brand"]
    semua = [x for v in IB.SINONIM.values() for x in v]
    assert len(semua) == len(set(semua))   # satu sinonim = satu medan
