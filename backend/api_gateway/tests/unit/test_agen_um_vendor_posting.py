"""Agen chat create_vendor_deposit SELALU memposting (simpan_dan_posting=true) -- 22 Okt 2026, putusan MASTER (temuan WORKSPACE):
dulu bawaan API = draf tanpa jurnal sementara chat mengumumkan "berhasil dicatat" (creates_journal=True) -> klaim palsu ke pengguna."""
import asyncio
import uuid
from datetime import date

from app.services.unified_agent.direct_action_registry import DIRECT_ACTIONS, apply_defaults
from app.schemas import vendor_deposits as VS


def _payload():
    p = apply_defaults("create_vendor_deposit", {"vendor_id": str(uuid.uuid4()), "vendor_name": "CV X", "bank_account_id": str(uuid.uuid4()),
                                                 "amount": 1000, "deposit_date": "2026-10-22"})
    cfg = DIRECT_ACTIONS["create_vendor_deposit"]
    buang = {f.name for f in cfg.fields if f.display_only}
    return {k: v for k, v in p.items() if k not in buang}  # pembuang display_only = jalur REST agen


def test_agen_create_mengirim_simpan_dan_posting_true():
    cfg = DIRECT_ACTIONS["create_vendor_deposit"]
    f = {x.name: x for x in cfg.fields}["simpan_dan_posting"]
    assert f.hidden and not f.editable and f.field_type == "boolean" and f.default == "true"
    assert cfg.creates_journal is True
    body = VS.VendorDepositCreate(**_payload())
    assert body.simpan_dan_posting is True, "agen membuat DRAF -> klaim berhasil dicatat palsu"


def test_tanpa_flag_bawaan_skema_tetap_draf():
    """Bawaan SKEMA tak diubah (pemanggil lain tetap): tanpa medan agen -> draf. Kontrol: membuang medan agen = tes di atas MERAH."""
    p = _payload(); p.pop("simpan_dan_posting")
    assert VS.VendorDepositCreate(**p).simpan_dan_posting is False
