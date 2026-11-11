"""laporan-fix-2: SATU definisi aliran kas (report_engine.aliran_kas.KONDISI_ALIRAN) untuk Arus Kas PSAK dan kartu dasbor, SADAR-PERIODE: asli yang dibalik
SESUDAH akhir rentang tetap aliran (rekonsiliasi dgn saldo POSTED); pembalik atas asli periode lalu tetap aliran; pasangan di dalam rentang saling hapus.
Paritas nyata (selisih rekonsiliasi == hanya anomali data lama) = uji salinan."""
import inspect

from app.services import dashboard_angka as DA
from app.services.report_engine import aliran_kas as AK
from app.services.report_engine import cash_flow as CF


def test_kondisi_sadar_periode_dan_tersaring_tenant():
    k = AK.KONDISI_ALIRAN
    assert "je.status = 'POSTED'" in k
    assert "r.journal_date <= $3" in k and "o.journal_date < $2" in k          # batas periode: akhir untuk pembalik, awal untuk asli
    assert "je.reversed_by_id IS NOT NULL" in k and "je.reversal_of_id IS NOT NULL" in k
    assert k.count("tenant_id = je.tenant_id") == 2                           # setiap subkueri tersaring tenant
    assert "'INVOICE_FULFILLMENT_REVERSAL'" in k and "is_effective_journal" not in k.replace("is_effective_journal)", "")  # tak retroaktif


def test_arus_kas_psak_dan_dasbor_memakai_kondisi_yang_sama():
    assert "{KONDISI_ALIRAN}" in inspect.getsource(CF.generate_cash_flow)       # PSAK menyisipkan kondisi yang sama (f-string), bukan salinan
    assert AK.KONDISI_ALIRAN in DA.SQL_ARUS_KAS_HARIAN                          # dasbor: SATU teks, tak ada salinan kedua
    assert "$2" in DA.SQL_ARUS_KAS_HARIAN and "$3" in DA.SQL_ARUS_KAS_HARIAN    # placeholder rentang yang dipakai kondisi
    assert "is_effective_journal" not in DA.SQL_ARUS_KAS_HARIAN                 # tak lagi efektif retroaktif
