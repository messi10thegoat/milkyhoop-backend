"""KONDISI ALIRAN KAS -- SATU definisi jurnal yang dihitung sebagai aliran, dipakai Arus Kas PSAK DAN kartu arus kas dasbor (laporan-fix-2; kisi dua sumber).

Aturan (sadar-periode; placeholder $2 = awal, $3 = akhir rentang SAMA dengan filter journal_date kueri pemanggil, alias je = journal_entries):
  * POSTED saja.
  * ASLI yang dibalik DIBUANG hanya bila pembaliknya (POSTED) bertanggal <= akhir rentang -- asli yang baru dibalik SESUDAH akhir rentang tetap aliran periode itu
    (saldo POSTED per akhir periode masih memuatnya; pembuangan retroaktif membuat rekonsiliasi kas awal + aliran = kas akhir selisih, terukur kaos 50.000).
  * PEMBALIK dibuang kecuali aslinya POSTED dan bertanggal SEBELUM awal rentang (pembalik atas asli periode lalu = aliran sungguhan periode ini). Pembalik atas asli
    yang tak berstatus POSTED (anomali data lama: asli dibalik jadi VOID, melanggar Law 2; kaos 16 jurnal) tetap dibuang seperti is_effective_journal; selisihnya
    menampak di rekonsiliasi sebagai cacat data (tiket data, bukan kode).
  * Jenis sumber pembalik lama (is_effective_journal butir c) dibuang.
Pasangan asli+pembalik di dalam rentang saling hapus (tak jadi masuk DAN keluar semu); pasangan melintasi batas rentang tampil sebagai aliran yang memang terjadi."""

KONDISI_ALIRAN = """je.status = 'POSTED'
          AND NOT (je.reversed_by_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM journal_entries r WHERE r.id = je.reversed_by_id AND r.tenant_id = je.tenant_id AND r.status = 'POSTED'
                  AND r.journal_date <= $3))
          AND NOT (je.reversal_of_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM journal_entries o WHERE o.id = je.reversal_of_id AND o.tenant_id = je.tenant_id AND o.status = 'POSTED'
                  AND o.journal_date < $2))
          AND je.source_type NOT IN ('INVOICE_REVERSAL', 'BILL_REVERSAL', 'INVOICE_FULFILLMENT_REVERSAL', 'SALES_INVOICE_COGS_REVERSAL')"""
