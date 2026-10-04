"""Pemeriksaan PIHAK saat dana satu pihak diterapkan ke dokumen (13 Sep 2026).

Kelas cacat yang ditutup: penerapan dana ke dokumen tanpa memastikan pihaknya sama.
Terukur di dua jalur dengan dua bentuk berbeda:
  - DP pelanggan -> faktur: TIDAK membandingkan sama sekali (lintas pelanggan 200, dieksekusi).
  - nota kredit -> faktur: membandingkan uuid.UUID dengan str -> selalu beda (fitur mati).

Tipe kolom pihak TIDAK seragam: sales_invoices/receive_payments/bills/... = uuid, tetapi
credit_notes.customer_id dan customer_deposits.customer_id = VARCHAR. Karena itu keduanya
dinormalisasi ke uuid.UUID dulu (kanonik: huruf besar/kecil & format tak berpengaruh), baru
dibandingkan. Membandingkan str() telanjang atau objek beda tipe adalah bentuk yang rusak.
"""
from decimal import Decimal
from typing import Optional, Union
from uuid import UUID

from fastapi import HTTPException
from . import teks_galat as tg


def normalisasi_pihak(nilai: Optional[Union[str, UUID]], label: str) -> UUID:
    """Ubah id pihak (uuid atau varchar) ke uuid.UUID kanonik.

    NULL / kosong / bukan uuid -> 400. Dana yang tak diketahui pemiliknya tidak boleh
    diterapkan ke dokumen siapa pun.
    """
    if nilai is None or (isinstance(nilai, str) and not nilai.strip()):
        raise HTTPException(status_code=400, detail=f"{label} tidak diketahui; dana tak bisa diterapkan")
    if isinstance(nilai, UUID):
        return nilai
    try:
        return UUID(str(nilai).strip())
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"{label} tidak valid; dana tak bisa diterapkan")


def pastikan_pihak_sama(
    pihak_sumber: UUID,
    pihak_dokumen: Optional[Union[str, UUID]],
    label_dokumen: str,
    jenis_pihak: str = "pelanggan",
) -> None:
    """400 bila pihak dokumen berbeda (atau tak diketahui) dari pihak pemilik dana."""
    dok = normalisasi_pihak(pihak_dokumen, f"{jenis_pihak.capitalize()} {label_dokumen}")
    if dok != pihak_sumber:
        raise HTTPException(
            status_code=400,
            detail=f"{label_dokumen} milik {jenis_pihak} lain; dana hanya bisa diterapkan ke dokumen {jenis_pihak} yang sama",
        )


async def pelanggan_kanonik_tenant(conn, tenant_id: str, nilai: Optional[Union[str, UUID]]) -> Optional[str]:
    """Pelanggan yang DIISI harus ada di tenant yang sama; kembalikan teks UUID kanonik (huruf kecil).

    Kosong/None -> None (pelanggan opsional di nota kredit & uang muka, perilaku hari ini dipertahankan).
    Bukan UUID (mis. NAMA "Toko Melati") -> 400. UUID pelanggan tenant lain / tak ada -> 400, dengan
    pesan YANG SAMA untuk keduanya (tak membocorkan keberadaan lintas tenant).
    Asal kebutuhan (13 Sep 2026): credit_notes.customer_id & customer_deposits.customer_id VARCHAR,
    pembuatnya menulis body mentah tanpa validasi -> CN-2608-0001 menyimpan nama sebagai id.
    """
    if nilai is None or (isinstance(nilai, str) and not nilai.strip()):
        return None
    try:
        uid = normalisasi_pihak(nilai, "Pelanggan")
    except HTTPException:
        # pesan normalisasi_pihak berkonteks "dana diterapkan"; di pembuat/penyunting dokumen salah konteks
        raise HTTPException(status_code=400, detail="Pelanggan tidak valid; pilih pelanggan dari daftar")
    ada = await conn.fetchval(
        "SELECT 1 FROM customers WHERE id = $1 AND tenant_id = $2", uid, tenant_id
    )
    if not ada:
        raise HTTPException(status_code=400, detail="Pelanggan tidak ditemukan")
    return str(uid)


def rupiah(nilai) -> str:
    """Rupiah baku (services/teks_galat.rp); dulu MEMBULATKAN ke rupiah utuh ('melebihi Rp 100.000' padahal 100.000,40)."""
    return tg.rp(nilai)
async def faktur_tenant_untuk_pelanggan(conn, tenant_id: str, invoice_id, pelanggan):
    """Faktur yang akan menerima atribusi nota kredit: harus ada di tenant ini DAN milik pelanggan nota kredit.

    Id karangan, id bukan uuid, dan id faktur tenant lain -> pesan YANG SAMA (tak membocorkan keberadaan).
    Unit B (14 Sep 2026): dulu pembuat/penyunting draf mencari faktur tanpa tenant & tanpa pelanggan ->
    atribusi piutang lintas pelanggan/tenant saat posting.
    """
    try:
        uid = invoice_id if isinstance(invoice_id, UUID) else UUID(str(invoice_id).strip())
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Faktur tidak ditemukan")
    row = await conn.fetchrow(
        "SELECT id, invoice_number, customer_id, status, journal_id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
        uid, tenant_id,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Faktur tidak ditemukan")
    if pelanggan is None or (isinstance(pelanggan, str) and not pelanggan.strip()):
        raise HTTPException(status_code=400, detail="Pilih pelanggan nota kredit sebelum mengaitkannya ke faktur.")
    if row["customer_id"] is None or str(row["customer_id"]) != str(pelanggan).strip().lower():
        raise HTTPException(
            status_code=400,
            detail="Faktur ini milik pelanggan lain; nota kredit hanya bisa dikaitkan ke faktur pelanggan yang sama.",
        )
    return row


async def pastikan_cn_muat_faktur(conn, tenant_id: str, faktur, total_cn, kecuali_cn=None) -> None:
    """Faktur harus sudah dibukukan & belum batal, dan nilai nota kredit <= NILAI FAKTUR dikurangi nota kredit lain
    yang tak-void atas faktur itu (draf ikut; NK ini sendiri dikecualikan).

    PUTUSAN PEMILIK 30 Sep 2026 (langsung di sesi BACKEND): "batas nota kredit = nilai faktur dikurangi nota kredit
    lain; kelebihan jadi saldo kredit pelanggan". Dulu batasnya SISA TAGIHAN (compute_ar_outstanding) -> retur atas
    faktur LUNAS selalu ditolak (uji nyata WORKSPACE INV-2609-0140). Bagian di atas sisa tagihan dibukukan saat posting
    ke Uang Muka Pelanggan (posting_nota_kredit), bukan piutang negatif."""
    if faktur["status"] in ("draft", "void") or faktur["journal_id"] is None:
        raise HTTPException(status_code=400, detail="Faktur belum dibukukan atau sudah dibatalkan.")
    nilai = await conn.fetchval(
        "SELECT total_amount FROM sales_invoices WHERE id = $1 AND tenant_id = $2", faktur["id"], tenant_id)
    lain = await conn.fetchval(
        """SELECT COALESCE(SUM(total_amount), 0) FROM credit_notes
           WHERE tenant_id = $1 AND original_invoice_id = $2 AND status <> 'void'
             AND ($3::uuid IS NULL OR id <> $3::uuid)""",
        tenant_id, faktur["id"], kecuali_cn)
    batas = Decimal(str(nilai or 0)) - Decimal(str(lain or 0))
    if Decimal(str(total_cn)) > batas:
        raise HTTPException(
            status_code=400,
            detail=(f"Nilai nota kredit ({rupiah(total_cn)}) melebihi yang masih bisa dinotakan dari faktur "
                    f"({rupiah(max(batas, Decimal('0')))}: nilai faktur {rupiah(nilai or 0)} dikurangi nota kredit lain "
                    f"{rupiah(lain or 0)})."),
        )


async def segarkan_cache_piutang_faktur(conn, tenant_id: str, invoice_id) -> None:
    """Cache amount_paid/status faktur + accounts_receivable dihitung ULANG dari compute_ar_outstanding (Rule 12).

    Bukan aritmetika sendiri: apply lama menambah nominal ke cache sementara fungsi inti tak bergerak
    (cache 120.000 vs compute 100.000). Baris hilang dari fungsi = outstanding 0.
    """
    f = await conn.fetchrow(
        "SELECT total_amount, status FROM sales_invoices WHERE id = $1 AND tenant_id = $2", invoice_id, tenant_id
    )
    if not f or f["status"] in ("draft", "void"):
        return
    out = await conn.fetchval(
        "SELECT COALESCE(SUM(outstanding), 0) FROM compute_ar_outstanding($1) WHERE invoice_id = $2",
        tenant_id, invoice_id,
    )
    out = max(Decimal("0"), Decimal(str(out)))
    dibayar = Decimal(str(f["total_amount"])) - out
    status = "paid" if out < Decimal("0.01") else ("partial" if dibayar > Decimal("0.005") else "posted")
    await conn.execute(
        "UPDATE sales_invoices SET amount_paid = $1, status = $2, updated_at = NOW() WHERE id = $3 AND tenant_id = $4",
        dibayar, status, invoice_id, tenant_id,
    )
    await conn.execute(
        """UPDATE accounts_receivable
           SET amount_paid = $1,
               status = CASE WHEN $2::numeric < 0.01 THEN 'PAID' WHEN $1::numeric > 0.005 THEN 'PARTIAL' ELSE 'OPEN' END,
               updated_at = NOW()
           WHERE source_id = $3 AND source_type = 'INVOICE' AND tenant_id = $4 AND status <> 'VOID'""",
        dibayar, out, invoice_id, tenant_id,
    )


async def segarkan_cache_hutang_tagihan(conn, tenant_id: str, bill_id) -> None:
    """Cache amount_paid/status TAGIHAN + accounts_payable dihitung ULANG dari compute_ap_outstanding.

    Kembaran segarkan_cache_piutang_faktur (26 Sep 2026, antrean 4b): dulu 6 penulis (pembayaran buat/post/void,
    uang muka vendor, nota kredit vendor, record_payment) masing-masing beraritmetika sendiri. Layar tagihan
    memang menurunkan status dari jurnal, tapi cache dibaca guard (void ditolak bila 'paid') dan laporan.
    Baris hilang dari fungsi = sisa 0. HANYA kolom status bayar (`status`) + amount_paid; `status_v2`
    (siklus hidup draft/posted/void, dibaca derive_doc_status & guard edit) TIDAK disentuh.
    WAJIB dipanggil SESUDAH jurnal POSTED dan bill_payments_v2.journal_id terisi (fungsi membaca lewat itu).
    """
    b = await conn.fetchrow(
        "SELECT amount, status, status_v2 FROM bills WHERE id = $1 AND tenant_id = $2", bill_id, tenant_id
    )
    if not b or b["status"] in ("draft", "void") or (b["status_v2"] or "") in ("draft", "void"):
        return
    out = await conn.fetchval(
        "SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding($1) WHERE bill_id = $2",
        tenant_id, bill_id,
    )
    out = max(Decimal("0"), Decimal(str(out)))
    dibayar = max(Decimal("0"), Decimal(str(b["amount"])) - out)
    status = "paid" if out < Decimal("0.01") else ("partial" if dibayar > Decimal("0.005") else "posted")
    await conn.execute(
        "UPDATE bills SET amount_paid = $1, status = $2, updated_at = NOW() WHERE id = $3 AND tenant_id = $4",
        dibayar, status, bill_id, tenant_id,
    )
    await conn.execute(
        """UPDATE accounts_payable
           SET amount_paid = $1,
               status = CASE WHEN $2::numeric < 0.01 THEN 'PAID' WHEN $1::numeric > 0.005 THEN 'PARTIAL' ELSE 'OPEN' END,
               updated_at = NOW()
           WHERE source_id = $3 AND source_type = 'BILL' AND tenant_id = $4 AND status <> 'VOID'""",
        dibayar, out, bill_id, tenant_id,
    )
