"""RCV-KUNCI-KLIEN / AS-G12 (23 Okt 2026, putusan pemilik): eksekusi aksi tertunda agen mengirim X-Idempotency-Key = agen:{pending_action_id}
(stabil per konfirmasi) -> konfirmasi ganda = satu dokumen; tanpa pending_action_id -> TANPA kunci (bukan "agen:None" bersama);
langkah posting lanjutan = kunci sendiri (:post)."""
import inspect

from app.routers import unified_chat as UC


def test_eksekusi_aksi_tertunda_mengirim_kunci_per_pending_action():
    src = inspect.getsource(UC)
    assert src.count('**({"X-Idempotency-Key": f"agen:{pending_action_id}"} if pending_action_id else {})') == 1
    assert src.count('**({"X-Idempotency-Key": f"agen:{pending_action_id}:post"} if pending_action_id else {})') == 1
    assert '"X-Idempotency-Key": f"agen:{pending_action_id}",' not in src   # kontrol: bentuk tanpa penjaga None tak ada


def test_kontrol_merah_kunci_none_tertangkap():
    pending_action_id = None
    h = {**({"X-Idempotency-Key": f"agen:{pending_action_id}"} if pending_action_id else {})}
    assert h == {}
    pending_action_id = "pa-1"
    h = {**({"X-Idempotency-Key": f"agen:{pending_action_id}"} if pending_action_id else {})}
    assert h == {"X-Idempotency-Key": "agen:pa-1"}
