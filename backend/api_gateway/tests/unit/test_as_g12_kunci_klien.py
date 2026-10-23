"""AS-G12 (23 Okt 2026): SATU pembaca kunci idempotensi klien untuk semua tulis (services/idem_buat.kunci_dari): header X-Idempotency-Key
(alias Idempotency-Key) dulu, medan badan `idempotency_key` = cadangan, keduanya beda -> 422 IDEMPOTENCY_KEY_MISMATCH (kontrak lama uang
muka). Tiga rute yang dulu menyimpang (SI fulfill = badan saja; SO to-invoice = badan dulu; UM create = logika sendiri) kini memakainya.
Agen: eksekusi aksi tertunda mengirim X-Idempotency-Key = agen:{pending_action_id} (+ ':post' untuk langkah posting)."""
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import idem_buat as IB

APP = Path(IB.__file__).resolve().parents[1]


class _Req:
    def __init__(self, **h):
        self.headers = h


@pytest.mark.parametrize("header,badan,harap", [
    ({"X-Idempotency-Key": "h1"}, None, "h1"), ({}, "b1", "b1"), ({"X-Idempotency-Key": "k"}, "k", "k"),
    ({"Idempotency-Key": " a "}, None, "a"), ({}, "   ", None), ({}, None, None), ({"X-Idempotency-Key": " "}, "b2", "b2")])
def test_matriks_pembaca(header, badan, harap):
    assert IB.kunci_dari(_Req(**header), badan) == harap


def test_header_dan_badan_beda_422_dan_terlalu_panjang_400():
    with pytest.raises(HTTPException) as e:
        IB.kunci_dari(_Req(**{"X-Idempotency-Key": "h"}), "b")
    assert e.value.status_code == 422 and e.value.detail["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    for h, b in (({"X-Idempotency-Key": "x" * 201}, None), ({}, "y" * 201)):
        with pytest.raises(HTTPException) as e2:
            IB.kunci_dari(_Req(**h), b)
        assert e2.value.status_code == 400


def test_tiga_rute_menyimpang_kini_memakai_pembaca_bersama():
    si = (APP / "routers/sales_invoices.py").read_text()
    so = (APP / "routers/sales_orders.py").read_text()
    dp = (APP / "routers/customer_deposits.py").read_text()
    assert 'client_idempotency_key = _kunci_dari(request, body.get("idempotency_key"))' in si
    assert "_kunci_klien = _kunci_dari(request, body.idempotency_key if body is not None else None)" in so
    assert "body.idempotency_key = _kunci_dari(request, body.idempotency_key)" in dp


# Pembaca langsung yang DIKECUALIKAN dengan nama + alasan (bukan pola luas). Kosongkan baris saat unitnya selesai.
PENGECUALIAN = {
    ("routers/receive_payments.py", 'request.headers.get("X-Idempotency-Key")'): "RCV-KUNCI-KLIEN (menunggu FE form klasik) menghapusnya",
    ("routers/sales_invoices.py", 'request.headers.get("X-Idempotency-Key")'): "AS-G9 SI /payments kunci default server (ikut RCV)",
}
POLA = [r'body\.get\(\s*["\']idempotency_key["\']\s*\)', r'\.idempotency_key\.strip\(\)', r'request\.headers\.get\(\s*["\'](X-)?Idempotency-Key["\']\s*\)']


def _pembaca_langsung():
    temuan = []
    for f in sorted(list((APP / "routers").glob("*.py")) + list((APP / "services").rglob("*.py"))):
        rel = str(f.relative_to(APP))
        if rel in ("services/idem_buat.py",):
            continue
        for n, baris in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if baris.lstrip().startswith("#") or "kunci_dari(" in baris:  # baris yang MENYERAHKAN ke pembaca bersama = sah
                continue
            for p in POLA:
                m = re.search(p, baris)
                if m and not any(rel == k[0] and k[1] in baris for k in PENGECUALIAN):
                    temuan.append(f"{rel}:{n}: {baris.strip()[:120]}")
    return temuan


def test_tak_ada_pembaca_kunci_langsung_di_luar_pembaca_bersama():
    assert _pembaca_langsung() == []


def test_kontrol_merah_pemindai():
    assert any(re.search(p, 'kunci = body.get("idempotency_key")') for p in POLA)
    assert any(re.search(p, 'k = request.headers.get("X-Idempotency-Key")') for p in POLA)


def test_agen_mengirim_kunci_per_pending_action():
    src = (APP / "routers/unified_chat.py").read_text()  # impor modul butuh kunci LLM -> baca teks
    assert src.count('**({"X-Idempotency-Key": f"agen:{pending_action_id}"} if pending_action_id else {})') == 1
    assert src.count('**({"X-Idempotency-Key": f"agen:{pending_action_id}:post"} if pending_action_id else {})') == 1
    pending_action_id = None
    assert {**({"X-Idempotency-Key": f"agen:{pending_action_id}"} if pending_action_id else {})} == {}
