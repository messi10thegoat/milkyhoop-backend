"""GP3a (23 Okt 2026): void pembayaran keluar membalik cermin bank yang dicari lewat JURNAL pembayaran + tenant (pola semua jalur void lain).
Dulu reference_type 'bill_payment' + id pembayaran -> cermin pembayaran lama record_payment (BP-, reference_type 'bill' + id TAGIHAN) tak
ketemu -> void BP- membalik jurnal tetapi cermin bank tertinggal. Bukti nyata: scripts/ops/uji-nyata-gp3a-cermin-void.sh (kaos, rollback)."""
import inspect
import re

from app.routers import bill_payments as BP
from app.services import bayar_keluar_rencana as BKR


def _sql_cermin():
    # AS-GP3 (24 Okt 2026): pencarian cermin pindah ke penentu bersama BKR.rencana_batal_bayar; penulis memakai r["cermin_asli"]
    src = inspect.getsource(BKR.rencana_batal_bayar)
    i = src.index('h["cermin_asli"] = await conn.fetchrow(')
    return " ".join(src[i:src.index("return h", i)].split())


def test_cermin_dicari_lewat_jurnal_dan_tenant():
    s = _sql_cermin()
    assert "WHERE bt.journal_id = $1 AND bt.tenant_id = $2" in s and 'p["journal_id"], tid)' in s
    assert "reference_type = 'bill_payment'" not in s
    assert 'original_btxn = r["cermin_asli"]' in inspect.getsource(BP.void_bill_payment)


def test_kontrol_merah_pola_lama_tertangkap():
    lama = "WHERE reference_type = 'bill_payment' AND reference_id = $1::uuid AND status != 'VOIDED'"
    assert re.search(r"reference_type = 'bill_payment'", lama) and "journal_id = $1" not in lama
