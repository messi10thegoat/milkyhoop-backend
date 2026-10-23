"""GP3a (23 Okt 2026): void pembayaran keluar membalik cermin bank yang dicari lewat JURNAL pembayaran + tenant (pola semua jalur void lain).
Dulu reference_type 'bill_payment' + id pembayaran -> cermin pembayaran lama record_payment (BP-, reference_type 'bill' + id TAGIHAN) tak
ketemu -> void BP- membalik jurnal tetapi cermin bank tertinggal. Bukti nyata: scripts/ops/uji-nyata-gp3a-cermin-void.sh (kaos, rollback)."""
import inspect
import re

from app.routers import bill_payments as BP


def _sql_cermin():
    src = inspect.getsource(BP.void_bill_payment)
    i = src.index("# C7: Bank mirror reversal")
    return " ".join(src[i:src.index("mirror_type =", i)].split())


def test_cermin_dicari_lewat_jurnal_dan_tenant():
    s = _sql_cermin()
    assert "WHERE journal_id = $1 AND tenant_id = $2" in s and 'payment["journal_id"], ctx["tenant_id"]' in s
    assert "reference_type = 'bill_payment'" not in s


def test_kontrol_merah_pola_lama_tertangkap():
    lama = "WHERE reference_type = 'bill_payment' AND reference_id = $1::uuid AND status != 'VOIDED'"
    assert re.search(r"reference_type = 'bill_payment'", lama) and "journal_id = $1" not in lama
