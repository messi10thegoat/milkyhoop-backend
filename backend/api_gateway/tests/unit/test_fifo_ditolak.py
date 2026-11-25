"""costing_method=fifo ditolak (422) di buat DAN ubah barang sampai FIFO hidup (putusan pemilik 24 Nov 2026)."""
import pytest
from pydantic import ValidationError

from app.schemas import items as S

MODEL = [c for c in vars(S).values() if isinstance(c, type) and hasattr(c, "model_fields") and "costing_method" in c.model_fields
         and "Response" not in c.__name__ and "Detail" not in c.__name__ and c.__module__ == S.__name__]


def test_dua_skema_masukan():
    assert {"CreateItemRequest", "UpdateItemRequest"} <= {c.__name__ for c in MODEL}


@pytest.mark.parametrize("nama", ["CreateItemRequest", "UpdateItemRequest"])
def test_fifo_ditolak_rata_rata_lolos(nama):
    M = getattr(S, nama)
    dasar = {"name": "X"} if nama.startswith("Create") else {}
    if nama.startswith("Create"):
        for k, f in M.model_fields.items():
            if f.is_required() and k not in dasar:
                dasar[k] = {"item_type": "goods"}.get(k, "Pcs")
    with pytest.raises(ValidationError) as e:
        M(**dasar, costing_method="fifo")
    assert "FIFO belum aktif" in str(e.value)
    assert M(**dasar, costing_method="weighted_average").costing_method == "weighted_average"
    assert M(**dasar).costing_method == ("weighted_average" if nama.startswith("Create") else None)
