"""
Reports Router - SAK EMKM Financial Reports
Endpoints for Neraca (Balance Sheet), Arus Kas (Cash Flow), and Laba Rugi (Income Statement)
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
import logging
import asyncpg
from datetime import datetime, timedelta
from calendar import monthrange

# Import centralized config
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# Database connection helper
async def get_db_connection():
    """Get database connection using centralized config"""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


# ========================================
# Helper Functions
# ========================================

def parse_periode(periode: str) -> tuple:
    """
    Parse periode string to date range.
    Supports: YYYY-MM (month), YYYY-Qn (quarter), YYYY (year)
    Returns: (start_date, end_date) as datetime
    """
    try:
        if '-Q' in periode:
            # Quarterly: 2024-Q4
            year, quarter = periode.split('-Q')
            year = int(year)
            quarter = int(quarter)
            start_month = (quarter - 1) * 3 + 1
            end_month = quarter * 3
            start_date = datetime(year, start_month, 1)
            _, last_day = monthrange(year, end_month)
            end_date = datetime(year, end_month, last_day, 23, 59, 59)
        elif len(periode) == 7:
            # Monthly: 2024-12
            year, month = map(int, periode.split('-'))
            start_date = datetime(year, month, 1)
            _, last_day = monthrange(year, month)
            end_date = datetime(year, month, last_day, 23, 59, 59)
        elif len(periode) == 4:
            # Yearly: 2024
            year = int(periode)
            start_date = datetime(year, 1, 1)
            end_date = datetime(year, 12, 31, 23, 59, 59)
        else:
            # Default to current month
            now = datetime.now()
            start_date = datetime(now.year, now.month, 1)
            _, last_day = monthrange(now.year, now.month)
            end_date = datetime(now.year, now.month, last_day, 23, 59, 59)

        return start_date, end_date
    except Exception as e:
        logger.error(f"Failed to parse periode '{periode}': {e}")
        # Default to current month
        now = datetime.now()
        start_date = datetime(now.year, now.month, 1)
        _, last_day = monthrange(now.year, now.month)
        end_date = datetime(now.year, now.month, last_day, 23, 59, 59)
        return start_date, end_date


# ========================================
# Response Models
# ========================================

class AsetLancarResponse(BaseModel):
    kas: int
    bank: int
    persediaan: int
    piutang_usaha: int
    beban_dibayar_dimuka: int
    uang_muka_pembelian: int
    total: int


class AsetTetapResponse(BaseModel):
    peralatan: int
    akum_penyusutan_peralatan: int
    kendaraan: int
    akum_penyusutan_kendaraan: int
    bangunan: int
    akum_penyusutan_bangunan: int
    tanah: int
    total: int
    total_neto: int


class KewajibanJangkaPendekResponse(BaseModel):
    hutang_usaha: int
    hutang_bank_jangka_pendek: int
    uang_muka_pelanggan: int
    hutang_pajak: int
    hutang_gaji: int
    total: int


class KewajibanJangkaPanjangResponse(BaseModel):
    hutang_bank: int
    total: int


class EkuitasResponse(BaseModel):
    modal_awal: int
    setor_modal: int
    prive: int
    laba_ditahan: int
    laba_periode_berjalan: int
    total: int


class NeracaResponse(BaseModel):
    periode: str
    tanggal: str
    aset_lancar: AsetLancarResponse
    aset_tetap: AsetTetapResponse
    total_aset: int
    kewajiban_jangka_pendek: KewajibanJangkaPendekResponse
    kewajiban_jangka_panjang: KewajibanJangkaPanjangResponse
    total_kewajiban: int
    ekuitas: EkuitasResponse
    is_balanced: bool


class ArusKasOperasiResponse(BaseModel):
    penerimaan_penjualan: int
    penerimaan_piutang: int
    penerimaan_lainnya: int
    total_penerimaan: int
    pembayaran_kulakan: int
    pembayaran_beban_operasi: int
    pembayaran_gaji: int
    pembayaran_pajak: int
    pembayaran_lainnya: int
    total_pengeluaran: int
    net_arus_kas_operasi: int


class ArusKasInvestasiResponse(BaseModel):
    penjualan_aset_tetap: int
    penerimaan_investasi: int
    total_penerimaan: int
    pembelian_aset_tetap: int
    pengeluaran_investasi: int
    total_pengeluaran: int
    net_arus_kas_investasi: int


class ArusKasPendanaanResponse(BaseModel):
    setor_modal: int
    penerimaan_pinjaman: int
    total_penerimaan: int
    prive: int
    pembayaran_pinjaman: int
    pembayaran_bunga: int
    total_pengeluaran: int
    net_arus_kas_pendanaan: int


class ArusKasResponse(BaseModel):
    periode: str
    tanggal_awal: str
    tanggal_akhir: str
    operasi: ArusKasOperasiResponse
    investasi: ArusKasInvestasiResponse
    pendanaan: ArusKasPendanaanResponse
    kenaikan_bersih_kas: int
    kas_awal_periode: int
    kas_akhir_periode: int


class LabaRugiResponse(BaseModel):
    periode: str
    pendapatan_penjualan: int
    diskon_penjualan: int
    pendapatan_lainnya: int
    total_pendapatan: int
    hpp: int
    laba_kotor: int
    beban_gaji: int
    beban_sewa: int
    beban_listrik: int
    beban_transportasi: int
    beban_penyusutan: int
    beban_lainnya: int
    total_beban: int
    laba_operasional: int
    pendapatan_bunga: int
    beban_bunga: int
    laba_bersih: int
    margin_laba_kotor: float
    margin_laba_bersih: float


# ========================================
# Endpoints
# ========================================

@router.get("/neraca/{periode}", response_model=NeracaResponse)
async def get_neraca(
    request: Request,
    periode: str
):
    """
    Get Laporan Posisi Keuangan (Neraca/Balance Sheet) for a given period.

    Period format:
    - YYYY-MM: Monthly (e.g., 2024-12)
    - YYYY-Qn: Quarterly (e.g., 2024-Q4)
    - YYYY: Yearly (e.g., 2024)
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)

        conn = await get_db_connection()
        try:
            # Query all transactions within the period
            query = """
                SELECT
                    jenis_transaksi,
                    total_nominal,
                    nominal_dibayar,
                    sisa_piutang_hutang,
                    metode_pembayaran,
                    pihak_type,
                    is_modal,
                    is_prive,
                    jenis_aset,
                    penyusutan_per_tahun,
                    umur_manfaat,
                    status_pembayaran,
                    kategori_beban
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2
                  AND timestamp <= $3
                  AND status = 'approved'
            """
            rows = await conn.fetch(query, tenant_id, start_date, end_date)

            # Calculate Aset Lancar
            kas = sum(r['total_nominal'] or 0 for r in rows if r['metode_pembayaran'] == 'tunai' and r['jenis_transaksi'] == 'penjualan')
            bank = sum(r['total_nominal'] or 0 for r in rows if r['metode_pembayaran'] in ['transfer', 'bank'] and r['jenis_transaksi'] == 'penjualan')
            piutang_usaha = sum(r['sisa_piutang_hutang'] or 0 for r in rows if (r['sisa_piutang_hutang'] or 0) > 0 and r['pihak_type'] == 'customer')

            # Get persediaan from persediaan table
            persediaan_query = """
                SELECT COALESCE(SUM(jumlah * nilai_per_unit), 0) as total
                FROM public.persediaan
                WHERE tenant_id = $1
            """
            persediaan_result = await conn.fetchrow(persediaan_query, tenant_id)
            persediaan = int(persediaan_result['total']) if persediaan_result else 0

            beban_dibayar_dimuka = sum(r['total_nominal'] or 0 for r in rows if r['jenis_aset'] and 'beban_dibayar_dimuka' in str(r['jenis_aset']))
            uang_muka_pembelian = sum(r['nominal_dibayar'] or 0 for r in rows if r['status_pembayaran'] == 'sebagian' and r['jenis_transaksi'] == 'pembelian')

            total_aset_lancar = kas + bank + persediaan + piutang_usaha + beban_dibayar_dimuka + uang_muka_pembelian

            # Calculate Aset Tetap
            peralatan = sum(r['total_nominal'] or 0 for r in rows if r['jenis_aset'] and 'peralatan' in str(r['jenis_aset']))
            kendaraan = sum(r['total_nominal'] or 0 for r in rows if r['jenis_aset'] and 'kendaraan' in str(r['jenis_aset']))
            bangunan = sum(r['total_nominal'] or 0 for r in rows if r['jenis_aset'] and 'bangunan' in str(r['jenis_aset']))
            tanah = sum(r['total_nominal'] or 0 for r in rows if r['jenis_aset'] and 'tanah' in str(r['jenis_aset']))

            # Accumulated depreciation
            akum_penyusutan = sum(
                ((r['penyusutan_per_tahun'] or 0) * (r['umur_manfaat'] or 0)) // 12
                for r in rows if r['penyusutan_per_tahun']
            )

            total_aset_tetap = peralatan + kendaraan + bangunan + tanah
            total_aset_tetap_neto = total_aset_tetap - akum_penyusutan
            total_aset = total_aset_lancar + total_aset_tetap_neto

            # Calculate Kewajiban
            hutang_usaha = sum(r['sisa_piutang_hutang'] or 0 for r in rows if (r['sisa_piutang_hutang'] or 0) > 0 and r['pihak_type'] == 'supplier')
            hutang_gaji = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'hutang_gaji' and r['status_pembayaran'] == 'belum_lunas')

            total_kewajiban_jangka_pendek = hutang_usaha + hutang_gaji
            total_kewajiban_jangka_panjang = 0  # TODO: Add bank loan tracking
            total_kewajiban = total_kewajiban_jangka_pendek + total_kewajiban_jangka_panjang

            # Calculate Ekuitas
            modal_awal = sum(r['total_nominal'] or 0 for r in rows if r['is_modal'] and r['jenis_transaksi'] == 'modal_awal')
            setor_modal = sum(r['total_nominal'] or 0 for r in rows if r['is_modal'] and r['jenis_transaksi'] == 'setor_modal')
            prive = sum(r['total_nominal'] or 0 for r in rows if r['is_prive'])

            # Calculate laba periode
            pendapatan = sum(r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'penjualan')
            beban = sum(r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'beban')
            hpp = sum(r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'pembelian')
            laba_periode_berjalan = pendapatan - hpp - beban

            total_ekuitas = modal_awal + setor_modal - prive + laba_periode_berjalan

            # Balance check
            is_balanced = abs(total_aset - (total_kewajiban + total_ekuitas)) < 1000

            logger.info(f"Neraca generated: tenant={tenant_id}, periode={periode}, aset={total_aset}, balanced={is_balanced}")

            return NeracaResponse(
                periode=periode,
                tanggal=end_date.strftime('%d %B %Y'),
                aset_lancar=AsetLancarResponse(
                    kas=kas,
                    bank=bank,
                    persediaan=persediaan,
                    piutang_usaha=piutang_usaha,
                    beban_dibayar_dimuka=beban_dibayar_dimuka,
                    uang_muka_pembelian=uang_muka_pembelian,
                    total=total_aset_lancar
                ),
                aset_tetap=AsetTetapResponse(
                    peralatan=peralatan,
                    akum_penyusutan_peralatan=akum_penyusutan // 3 if akum_penyusutan else 0,
                    kendaraan=kendaraan,
                    akum_penyusutan_kendaraan=akum_penyusutan // 3 if akum_penyusutan else 0,
                    bangunan=bangunan,
                    akum_penyusutan_bangunan=akum_penyusutan // 3 if akum_penyusutan else 0,
                    tanah=tanah,
                    total=total_aset_tetap,
                    total_neto=total_aset_tetap_neto
                ),
                total_aset=total_aset,
                kewajiban_jangka_pendek=KewajibanJangkaPendekResponse(
                    hutang_usaha=hutang_usaha,
                    hutang_bank_jangka_pendek=0,
                    uang_muka_pelanggan=0,
                    hutang_pajak=0,
                    hutang_gaji=hutang_gaji,
                    total=total_kewajiban_jangka_pendek
                ),
                kewajiban_jangka_panjang=KewajibanJangkaPanjangResponse(
                    hutang_bank=0,
                    total=total_kewajiban_jangka_panjang
                ),
                total_kewajiban=total_kewajiban,
                ekuitas=EkuitasResponse(
                    modal_awal=modal_awal,
                    setor_modal=setor_modal,
                    prive=prive,
                    laba_ditahan=0,
                    laba_periode_berjalan=laba_periode_berjalan,
                    total=total_ekuitas
                ),
                is_balanced=is_balanced
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get neraca error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate neraca report")


@router.get("/arus-kas/{periode}", response_model=ArusKasResponse)
async def get_arus_kas(
    request: Request,
    periode: str
):
    """
    Get Laporan Arus Kas (Cash Flow Statement) for a given period.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)

        conn = await get_db_connection()
        try:
            # Query all transactions
            query = """
                SELECT
                    jenis_transaksi,
                    total_nominal,
                    nominal_dibayar,
                    metode_pembayaran,
                    is_modal,
                    is_prive,
                    jenis_aset,
                    kategori_beban,
                    kategori_arus_kas
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2
                  AND timestamp <= $3
                  AND status = 'approved'
            """
            rows = await conn.fetch(query, tenant_id, start_date, end_date)

            # ARUS KAS OPERASI
            penerimaan_penjualan = sum(r['nominal_dibayar'] or r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'penjualan')
            penerimaan_piutang = 0  # TODO: Track piutang payments
            penerimaan_lainnya = 0
            total_penerimaan_operasi = penerimaan_penjualan + penerimaan_piutang + penerimaan_lainnya

            pembayaran_kulakan = sum(r['nominal_dibayar'] or r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'pembelian')
            pembayaran_gaji = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'beban_gaji')
            pembayaran_beban_lain = sum(r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'beban' and r['kategori_beban'] != 'beban_gaji')
            total_pengeluaran_operasi = pembayaran_kulakan + pembayaran_gaji + pembayaran_beban_lain

            net_arus_kas_operasi = total_penerimaan_operasi - total_pengeluaran_operasi

            # ARUS KAS INVESTASI
            pembelian_aset = sum(r['total_nominal'] or 0 for r in rows if r['jenis_aset'] and 'aset_tetap' in str(r['jenis_aset']))
            penjualan_aset = 0
            total_penerimaan_investasi = penjualan_aset
            total_pengeluaran_investasi = pembelian_aset
            net_arus_kas_investasi = total_penerimaan_investasi - total_pengeluaran_investasi

            # ARUS KAS PENDANAAN
            setor_modal = sum(r['total_nominal'] or 0 for r in rows if r['is_modal'])
            penerimaan_pinjaman = 0
            total_penerimaan_pendanaan = setor_modal + penerimaan_pinjaman

            prive = sum(r['total_nominal'] or 0 for r in rows if r['is_prive'])
            pembayaran_pinjaman = 0
            pembayaran_bunga = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'beban_bunga')
            total_pengeluaran_pendanaan = prive + pembayaran_pinjaman + pembayaran_bunga
            net_arus_kas_pendanaan = total_penerimaan_pendanaan - total_pengeluaran_pendanaan

            # TOTAL
            kenaikan_bersih_kas = net_arus_kas_operasi + net_arus_kas_investasi + net_arus_kas_pendanaan
            kas_awal_periode = 0  # TODO: Get from previous period
            kas_akhir_periode = kas_awal_periode + kenaikan_bersih_kas

            logger.info(f"Arus Kas generated: tenant={tenant_id}, periode={periode}, kas_akhir={kas_akhir_periode}")

            return ArusKasResponse(
                periode=periode,
                tanggal_awal=start_date.strftime('%d %B %Y'),
                tanggal_akhir=end_date.strftime('%d %B %Y'),
                operasi=ArusKasOperasiResponse(
                    penerimaan_penjualan=penerimaan_penjualan,
                    penerimaan_piutang=penerimaan_piutang,
                    penerimaan_lainnya=penerimaan_lainnya,
                    total_penerimaan=total_penerimaan_operasi,
                    pembayaran_kulakan=pembayaran_kulakan,
                    pembayaran_beban_operasi=pembayaran_beban_lain,
                    pembayaran_gaji=pembayaran_gaji,
                    pembayaran_pajak=0,
                    pembayaran_lainnya=0,
                    total_pengeluaran=total_pengeluaran_operasi,
                    net_arus_kas_operasi=net_arus_kas_operasi
                ),
                investasi=ArusKasInvestasiResponse(
                    penjualan_aset_tetap=penjualan_aset,
                    penerimaan_investasi=0,
                    total_penerimaan=total_penerimaan_investasi,
                    pembelian_aset_tetap=pembelian_aset,
                    pengeluaran_investasi=0,
                    total_pengeluaran=total_pengeluaran_investasi,
                    net_arus_kas_investasi=net_arus_kas_investasi
                ),
                pendanaan=ArusKasPendanaanResponse(
                    setor_modal=setor_modal,
                    penerimaan_pinjaman=penerimaan_pinjaman,
                    total_penerimaan=total_penerimaan_pendanaan,
                    prive=prive,
                    pembayaran_pinjaman=pembayaran_pinjaman,
                    pembayaran_bunga=pembayaran_bunga,
                    total_pengeluaran=total_pengeluaran_pendanaan,
                    net_arus_kas_pendanaan=net_arus_kas_pendanaan
                ),
                kenaikan_bersih_kas=kenaikan_bersih_kas,
                kas_awal_periode=kas_awal_periode,
                kas_akhir_periode=kas_akhir_periode
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get arus kas error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate arus kas report")


@router.get("/laba-rugi/{periode}", response_model=LabaRugiResponse)
async def get_laba_rugi(
    request: Request,
    periode: str
):
    """
    Get Laporan Laba Rugi (Income Statement) for a given period.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)

        conn = await get_db_connection()
        try:
            # Query all transactions
            query = """
                SELECT
                    jenis_transaksi,
                    total_nominal,
                    discount_amount,
                    kategori_beban,
                    penyusutan_per_tahun
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2
                  AND timestamp <= $3
                  AND status = 'approved'
            """
            rows = await conn.fetch(query, tenant_id, start_date, end_date)

            # PENDAPATAN
            pendapatan_penjualan = sum(r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'penjualan')
            diskon_penjualan = sum(r['discount_amount'] or 0 for r in rows if r['jenis_transaksi'] == 'penjualan')
            pendapatan_lainnya = 0  # TODO: Add other income tracking
            total_pendapatan = pendapatan_penjualan - diskon_penjualan + pendapatan_lainnya

            # HPP (Harga Pokok Penjualan)
            hpp = sum(r['total_nominal'] or 0 for r in rows if r['jenis_transaksi'] == 'pembelian')

            # LABA KOTOR
            laba_kotor = total_pendapatan - hpp

            # BEBAN OPERASIONAL
            beban_gaji = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'beban_gaji')
            beban_sewa = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'beban_sewa')
            beban_listrik = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] in ['beban_listrik', 'beban_utilitas'])
            beban_transportasi = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'beban_transportasi')
            beban_penyusutan = sum((r['penyusutan_per_tahun'] or 0) // 12 for r in rows if r['penyusutan_per_tahun'])
            beban_lainnya = sum(
                r['total_nominal'] or 0 for r in rows
                if r['jenis_transaksi'] == 'beban'
                and r['kategori_beban'] not in ['beban_gaji', 'beban_sewa', 'beban_listrik', 'beban_utilitas', 'beban_transportasi', 'beban_bunga']
            )

            total_beban = beban_gaji + beban_sewa + beban_listrik + beban_transportasi + beban_penyusutan + beban_lainnya

            # LABA OPERASIONAL
            laba_operasional = laba_kotor - total_beban

            # PENDAPATAN & BEBAN LAIN
            pendapatan_bunga = 0  # TODO: Add interest income tracking
            beban_bunga = sum(r['total_nominal'] or 0 for r in rows if r['kategori_beban'] == 'beban_bunga')

            # LABA BERSIH
            laba_bersih = laba_operasional + pendapatan_bunga - beban_bunga

            # MARGIN
            margin_laba_kotor = (laba_kotor / total_pendapatan * 100) if total_pendapatan > 0 else 0.0
            margin_laba_bersih = (laba_bersih / total_pendapatan * 100) if total_pendapatan > 0 else 0.0

            logger.info(f"Laba Rugi generated: tenant={tenant_id}, periode={periode}, laba_bersih={laba_bersih}")

            return LabaRugiResponse(
                periode=periode,
                pendapatan_penjualan=pendapatan_penjualan,
                diskon_penjualan=diskon_penjualan,
                pendapatan_lainnya=pendapatan_lainnya,
                total_pendapatan=total_pendapatan,
                hpp=hpp,
                laba_kotor=laba_kotor,
                beban_gaji=beban_gaji,
                beban_sewa=beban_sewa,
                beban_listrik=beban_listrik,
                beban_transportasi=beban_transportasi,
                beban_penyusutan=beban_penyusutan,
                beban_lainnya=beban_lainnya,
                total_beban=total_beban,
                laba_operasional=laba_operasional,
                pendapatan_bunga=pendapatan_bunga,
                beban_bunga=beban_bunga,
                laba_bersih=laba_bersih,
                margin_laba_kotor=round(margin_laba_kotor, 2),
                margin_laba_bersih=round(margin_laba_bersih, 2)
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get laba rugi error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate laba rugi report")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "reports_router"}
