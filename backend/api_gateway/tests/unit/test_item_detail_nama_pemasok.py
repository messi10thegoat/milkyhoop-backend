"""BUG-454: GET /api/items/{id} membawa preferred_vendor_name (join vendors se-tenant)."""
import pathlib

SRC = (pathlib.Path(__file__).parents[2] / "app/routers/items.py").read_text()


def test_detail_join_vendor_dan_medan_nama():
    assert "pv.name AS preferred_vendor_name" in SRC
    assert "pv.tenant_id = p.tenant_id" in SRC
    assert '"preferred_vendor_name": row.get("preferred_vendor_name")' in SRC
