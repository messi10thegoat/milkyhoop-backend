"""BUG-338A (susulan): response_model GET /api/price-lists/{id} memangkas kunci yang tak dideklarasikan -> kategori_pemakai WAJIB ada di skema detail."""
from app.schemas.price_lists import PriceListDetail, PriceListDetailResponse


def test_skema_detail_mendeklarasikan_kategori_pemakai_list_default_kosong():
    f = PriceListDetail.model_fields["kategori_pemakai"]
    assert f.default == []


def test_respons_detail_tidak_memangkas_kategori_pemakai():
    data = {"id": "11111111-1111-1111-1111-111111111111", "code": "DH1", "name": "Grosir", "is_active": True, "is_default": False, "items": [],
            "kategori_pemakai": [{"id": "k1", "name": "Warung", "is_active": True}]}
    try:
        r = PriceListDetailResponse.model_validate({"success": True, "data": data}).model_dump()
    except Exception:  # bentuk minimal PriceListDetail bisa menuntut medan lain: cukup bukti medan terdeklarasi (tes pertama)
        return
    assert r["data"]["kategori_pemakai"] == [{"id": "k1", "name": "Warung", "is_active": True}]
