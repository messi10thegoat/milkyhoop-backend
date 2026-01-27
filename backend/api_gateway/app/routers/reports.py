"""
Reports Router - SAK EMKM Financial Reports
Endpoints for Neraca (Balance Sheet), Arus Kas (Cash Flow), Laba Rugi (Income Statement),
and Trial Balance using the Accounting Kernel.
Supports both Cash and Accrual accounting basis.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional, List, Literal, Dict, Any
import logging
import asyncpg
from datetime import datetime, date, timedelta
from calendar import monthrange
import uuid

# Import centralized config
from ..config import settings

# Import AccountingFacade for proper double-entry reporting
from accounting_kernel.integration.facade import AccountingFacade

# Import accounting settings schemas
from ..schemas.accounting_settings import (
    AccountingSettingsResponse,
    UpdateAccountingSettingsRequest,
    AccountingSettingsDetailResponse,
    ProfitLossReport,
    ProfitLossReportResponse,
    ReportAccountLine,
    RevenueExpenseSection,
    ComparisonLine,
    ComparisonSection,
    CashAccrualComparisonReport,
    ComparisonReportResponse,
    TimingDifferencesReport,
    TimingDifferencesResponse,
    UnpaidInvoiceItem,
    UnpaidBillItem,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool for AccountingFacade (initialized on first request)
_pool: Optional[asyncpg.Pool] = None
_facade: Optional[AccountingFacade] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool for AccountingFacade."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
    return _pool


async def get_facade() -> AccountingFacade:
    """Get AccountingFacade instance with connection pool."""
    global _facade
    pool = await get_pool()
    if _facade is None:
        _facade = AccountingFacade(pool)
    return _facade


# Database connection helper (legacy - for existing endpoints)
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
        # Convert to unix timestamp (milliseconds) for bigint column
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)

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
            rows = await conn.fetch(query, tenant_id, start_ts, end_ts)

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
        # Convert to unix timestamp (milliseconds) for bigint column
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)

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
            rows = await conn.fetch(query, tenant_id, start_ts, end_ts)

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
    periode: str,
    basis: Optional[Literal["cash", "accrual"]] = Query(
        default=None,
        description="Accounting basis: 'cash' or 'accrual'. If not specified, uses tenant's default setting."
    )
):
    """
    Get Laporan Laba Rugi (Income Statement) for a given period.

    Accounting Basis:
    - **accrual**: Revenue recognized on invoice date, expenses on bill date
    - **cash**: Revenue recognized when payment received, expenses when payment made
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)
        # Convert to unix timestamp (milliseconds) for bigint column
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)

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
            rows = await conn.fetch(query, tenant_id, start_ts, end_ts)

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


# ========================================
# Trial Balance (using AccountingFacade)
# ========================================

class TrialBalanceAccountRow(BaseModel):
    """Single account row in trial balance."""
    account_id: str
    account_code: str
    account_name: str
    account_type: str
    normal_balance: str
    total_debit: float
    total_credit: float
    balance: float


class TrialBalanceResponse(BaseModel):
    """Trial Balance report response."""
    tenant_id: str
    as_of_date: str
    period_id: Optional[str] = None
    total_debit: float
    total_credit: float
    is_balanced: bool
    account_count: int
    accounts: List[TrialBalanceAccountRow]


class TrialBalanceSummaryByType(BaseModel):
    """Summary for a single account type."""
    total_debit: float
    total_credit: float
    balance: float
    account_count: int


class TrialBalanceSummaryResponse(BaseModel):
    """Trial Balance summary grouped by account type."""
    tenant_id: str
    as_of_date: str
    total_debit: float
    total_credit: float
    is_balanced: bool
    by_type: dict


@router.get("/trial-balance", response_model=TrialBalanceResponse)
async def get_trial_balance(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """
    Get Trial Balance report using the Accounting Kernel.

    Trial balance shows all accounts with their debit/credit totals
    from journal_entries (proper double-entry bookkeeping data).

    This endpoint uses AccountingFacade.get_trial_balance() which:
    - Reads from journal_entries and journal_lines tables
    - Calculates proper debit/credit balances per account
    - Verifies that total_debit == total_credit (balanced)

    Query Parameters:
    - as_of: Date for balance calculation (default: today)
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Use AccountingFacade for proper double-entry data
        facade = await get_facade()

        # Get trial balance from journal_entries
        result = await facade.get_trial_balance(
            tenant_id=tenant_id,
            as_of_date=as_of
        )

        logger.info(
            f"Trial Balance generated: tenant={tenant_id}, "
            f"as_of={result['as_of_date']}, "
            f"debit={result['total_debit']}, "
            f"credit={result['total_credit']}, "
            f"balanced={result['is_balanced']}"
        )

        return TrialBalanceResponse(
            tenant_id=result['tenant_id'],
            as_of_date=result['as_of_date'],
            period_id=result.get('period_id'),
            total_debit=result['total_debit'],
            total_credit=result['total_credit'],
            is_balanced=result['is_balanced'],
            account_count=result['account_count'],
            accounts=[
                TrialBalanceAccountRow(**acc)
                for acc in result['accounts']
            ]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get trial balance error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate trial balance report")


@router.get("/trial-balance/summary", response_model=TrialBalanceSummaryResponse)
async def get_trial_balance_summary(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """
    Get Trial Balance summary grouped by account type.

    Useful for quick overview and balance sheet preparation.
    Returns totals for ASSET, LIABILITY, EQUITY, INCOME, EXPENSE.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        facade = await get_facade()
        result = await facade.get_trial_balance_summary(
            tenant_id=tenant_id,
            as_of_date=as_of
        )

        logger.info(
            f"Trial Balance Summary generated: tenant={tenant_id}, "
            f"balanced={result['is_balanced']}"
        )

        return TrialBalanceSummaryResponse(
            tenant_id=result['tenant_id'],
            as_of_date=result['as_of_date'],
            total_debit=result['total_debit'],
            total_credit=result['total_credit'],
            is_balanced=result['is_balanced'],
            by_type=result['by_type']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get trial balance summary error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate trial balance summary")


# ========================================
# Accounting Settings
# ========================================

@router.get("/accounting-settings", response_model=AccountingSettingsDetailResponse)
async def get_accounting_settings(request: Request):
    """
    Get tenant's accounting settings.

    Returns current settings including:
    - default_report_basis: 'cash' or 'accrual'
    - fiscal_year_start_month: 1-12
    - base_currency_code: 3-letter currency code
    - number/date formatting preferences
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            # Get or create settings for tenant
            row = await conn.fetchrow("""
                SELECT * FROM accounting_settings WHERE tenant_id = $1
            """, tenant_id)

            if not row:
                # Create default settings
                new_id = str(uuid.uuid4())
                await conn.execute("""
                    INSERT INTO accounting_settings (id, tenant_id)
                    VALUES ($1, $2)
                """, new_id, tenant_id)

                row = await conn.fetchrow("""
                    SELECT * FROM accounting_settings WHERE tenant_id = $1
                """, tenant_id)

            return AccountingSettingsDetailResponse(
                success=True,
                data=AccountingSettingsResponse(
                    id=str(row['id']),
                    tenant_id=row['tenant_id'],
                    default_report_basis=row['default_report_basis'] or 'accrual',
                    fiscal_year_start_month=row['fiscal_year_start_month'] or 1,
                    base_currency_code=row['base_currency_code'] or 'IDR',
                    decimal_places=row['decimal_places'] or 0,
                    thousand_separator=row['thousand_separator'] or '.',
                    decimal_separator=row['decimal_separator'] or ',',
                    date_format=row['date_format'] or 'DD/MM/YYYY',
                    created_at=row['created_at'].isoformat() if row['created_at'] else '',
                    updated_at=row['updated_at'].isoformat() if row['updated_at'] else ''
                )
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get accounting settings error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get accounting settings")


@router.patch("/accounting-settings", response_model=AccountingSettingsDetailResponse)
async def update_accounting_settings(
    request: Request,
    data: UpdateAccountingSettingsRequest
):
    """
    Update tenant's accounting settings.

    Updatable fields:
    - default_report_basis: 'cash' or 'accrual'
    - fiscal_year_start_month: 1-12
    - base_currency_code: 3-letter currency code
    - decimal_places, thousand_separator, decimal_separator
    - date_format
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            # Ensure settings exist
            existing = await conn.fetchrow("""
                SELECT id FROM accounting_settings WHERE tenant_id = $1
            """, tenant_id)

            if not existing:
                new_id = str(uuid.uuid4())
                await conn.execute("""
                    INSERT INTO accounting_settings (id, tenant_id)
                    VALUES ($1, $2)
                """, new_id, tenant_id)

            # Build dynamic update
            updates = []
            params = [tenant_id]
            param_idx = 2

            if data.default_report_basis is not None:
                updates.append(f"default_report_basis = ${param_idx}")
                params.append(data.default_report_basis)
                param_idx += 1

            if data.fiscal_year_start_month is not None:
                updates.append(f"fiscal_year_start_month = ${param_idx}")
                params.append(data.fiscal_year_start_month)
                param_idx += 1

            if data.base_currency_code is not None:
                updates.append(f"base_currency_code = ${param_idx}")
                params.append(data.base_currency_code)
                param_idx += 1

            if data.decimal_places is not None:
                updates.append(f"decimal_places = ${param_idx}")
                params.append(data.decimal_places)
                param_idx += 1

            if data.thousand_separator is not None:
                updates.append(f"thousand_separator = ${param_idx}")
                params.append(data.thousand_separator)
                param_idx += 1

            if data.decimal_separator is not None:
                updates.append(f"decimal_separator = ${param_idx}")
                params.append(data.decimal_separator)
                param_idx += 1

            if data.date_format is not None:
                updates.append(f"date_format = ${param_idx}")
                params.append(data.date_format)
                param_idx += 1

            if updates:
                updates.append("updated_at = NOW()")
                update_sql = f"""
                    UPDATE accounting_settings
                    SET {', '.join(updates)}
                    WHERE tenant_id = $1
                """
                await conn.execute(update_sql, *params)

            # Fetch updated settings
            row = await conn.fetchrow("""
                SELECT * FROM accounting_settings WHERE tenant_id = $1
            """, tenant_id)

            logger.info(f"Accounting settings updated: tenant={tenant_id}")

            return AccountingSettingsDetailResponse(
                success=True,
                data=AccountingSettingsResponse(
                    id=str(row['id']),
                    tenant_id=row['tenant_id'],
                    default_report_basis=row['default_report_basis'] or 'accrual',
                    fiscal_year_start_month=row['fiscal_year_start_month'] or 1,
                    base_currency_code=row['base_currency_code'] or 'IDR',
                    decimal_places=row['decimal_places'] or 0,
                    thousand_separator=row['thousand_separator'] or '.',
                    decimal_separator=row['decimal_separator'] or ',',
                    date_format=row['date_format'] or 'DD/MM/YYYY',
                    created_at=row['created_at'].isoformat() if row['created_at'] else '',
                    updated_at=row['updated_at'].isoformat() if row['updated_at'] else ''
                )
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update accounting settings error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update accounting settings")


# ========================================
# Cash/Accrual Profit & Loss (Journal-based)
# ========================================

@router.get("/profit-loss/{periode}", response_model=ProfitLossReportResponse)
async def get_profit_loss_by_basis(
    request: Request,
    periode: str,
    basis: Optional[Literal["cash", "accrual"]] = Query(
        default=None,
        description="Accounting basis: 'cash' or 'accrual'. If not specified, uses tenant's default."
    )
):
    """
    Get Profit & Loss report using journal entries with specified accounting basis.

    This endpoint uses the proper double-entry journal data rather than legacy transaksi_harian.

    **Accrual basis** (default):
    - Revenue recognized when invoice is issued
    - Expenses recognized when bill is recorded

    **Cash basis**:
    - Revenue recognized when payment is received
    - Expenses recognized when payment is made
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)
        # Convert to unix timestamp (milliseconds) for bigint column
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)
        conn = await get_db_connection()

        try:
            # Get default basis if not specified
            if basis is None:
                settings_row = await conn.fetchrow("""
                    SELECT default_report_basis FROM accounting_settings
                    WHERE tenant_id = $1
                """, tenant_id)
                basis = settings_row['default_report_basis'] if settings_row else 'accrual'

            # Set tenant context for RLS
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)

            # Query revenue using helper function
            revenue_rows = await conn.fetch("""
                SELECT * FROM get_revenue_by_basis($1, $2, $3, $4)
            """, tenant_id, start_date.date(), end_date.date(), basis)

            revenue_accounts = [
                ReportAccountLine(
                    account_id=str(r['account_id']),
                    account_code=r['account_code'],
                    account_name=r['account_name'],
                    amount=int(r['total_amount'])
                ) for r in revenue_rows
            ]
            total_revenue = sum(int(r['total_amount']) for r in revenue_rows)

            # Query expenses using helper function
            expense_rows = await conn.fetch("""
                SELECT * FROM get_expenses_by_basis($1, $2, $3, $4)
            """, tenant_id, start_date.date(), end_date.date(), basis)

            # Separate into categories
            cogs_accounts = []
            operating_expense_accounts = []
            other_expense_accounts = []
            total_cogs = 0
            total_operating_expenses = 0
            total_other_expenses = 0

            for r in expense_rows:
                account_type = await conn.fetchval("""
                    SELECT account_type FROM chart_of_accounts WHERE id = $1
                """, r['account_id'])

                line = ReportAccountLine(
                    account_id=str(r['account_id']),
                    account_code=r['account_code'],
                    account_name=r['account_name'],
                    amount=int(r['total_amount'])
                )

                if account_type == 'COGS':
                    cogs_accounts.append(line)
                    total_cogs += int(r['total_amount'])
                elif account_type == 'OTHER_EXPENSE':
                    other_expense_accounts.append(line)
                    total_other_expenses += int(r['total_amount'])
                else:  # EXPENSE
                    operating_expense_accounts.append(line)
                    total_operating_expenses += int(r['total_amount'])

            # Query other income (interest, forex gains, etc.)
            other_income_rows = await conn.fetch("""
                SELECT
                    jl.account_id,
                    coa.account_code,
                    coa.account_name,
                    SUM(jl.credit - jl.debit)::BIGINT as total_amount
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                AND je.entry_date BETWEEN $2 AND $3
                AND coa.account_type = 'OTHER_INCOME'
                GROUP BY jl.account_id, coa.account_code, coa.account_name
                HAVING SUM(jl.credit - jl.debit) != 0
            """, tenant_id, start_date.date(), end_date.date())

            other_income_accounts = [
                ReportAccountLine(
                    account_id=str(r['account_id']),
                    account_code=r['account_code'],
                    account_name=r['account_name'],
                    amount=int(r['total_amount'])
                ) for r in other_income_rows
            ]
            total_other_income = sum(int(r['total_amount']) for r in other_income_rows)

            # Calculate totals
            gross_profit = total_revenue - total_cogs
            operating_income = gross_profit - total_operating_expenses
            net_income_before_tax = operating_income + total_other_income - total_other_expenses
            net_income = net_income_before_tax  # Tax handling can be added later

            logger.info(f"Profit/Loss generated: tenant={tenant_id}, periode={periode}, basis={basis}, net_income={net_income}")

            return ProfitLossReportResponse(
                success=True,
                data=ProfitLossReport(
                    period=periode,
                    basis=basis,
                    revenue=RevenueExpenseSection(
                        accounts=revenue_accounts,
                        total=total_revenue
                    ),
                    cost_of_goods_sold=RevenueExpenseSection(
                        accounts=cogs_accounts,
                        total=total_cogs
                    ),
                    gross_profit=gross_profit,
                    operating_expenses=RevenueExpenseSection(
                        accounts=operating_expense_accounts,
                        total=total_operating_expenses
                    ),
                    operating_income=operating_income,
                    other_income=RevenueExpenseSection(
                        accounts=other_income_accounts,
                        total=total_other_income
                    ),
                    other_expenses=RevenueExpenseSection(
                        accounts=other_expense_accounts,
                        total=total_other_expenses
                    ),
                    net_income_before_tax=net_income_before_tax,
                    tax_expense=0,
                    net_income=net_income
                )
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get profit/loss error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate profit/loss report")


# ========================================
# Cash vs Accrual Comparison
# ========================================

@router.get("/comparison/{periode}", response_model=ComparisonReportResponse)
async def get_cash_accrual_comparison(
    request: Request,
    periode: str
):
    """
    Get side-by-side comparison of Cash vs Accrual basis for a period.

    Useful for understanding timing differences and their impact on reported income.
    Shows the difference in revenue, expenses, and net income between the two methods.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)
        # Convert to unix timestamp (milliseconds) for bigint column
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)
        conn = await get_db_connection()

        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)

            # Get revenue for both bases
            cash_revenue = await conn.fetch("""
                SELECT * FROM get_revenue_by_basis($1, $2, $3, 'cash')
            """, tenant_id, start_date.date(), end_date.date())

            accrual_revenue = await conn.fetch("""
                SELECT * FROM get_revenue_by_basis($1, $2, $3, 'accrual')
            """, tenant_id, start_date.date(), end_date.date())

            # Get expenses for both bases
            cash_expenses = await conn.fetch("""
                SELECT * FROM get_expenses_by_basis($1, $2, $3, 'cash')
            """, tenant_id, start_date.date(), end_date.date())

            accrual_expenses = await conn.fetch("""
                SELECT * FROM get_expenses_by_basis($1, $2, $3, 'accrual')
            """, tenant_id, start_date.date(), end_date.date())

            # Build comparison dictionaries
            def build_comparison(cash_rows, accrual_rows) -> ComparisonSection:
                all_accounts = {}

                for r in cash_rows:
                    key = str(r['account_id'])
                    all_accounts[key] = {
                        'account_id': key,
                        'account_code': r['account_code'],
                        'account_name': r['account_name'],
                        'cash_amount': int(r['total_amount']),
                        'accrual_amount': 0
                    }

                for r in accrual_rows:
                    key = str(r['account_id'])
                    if key in all_accounts:
                        all_accounts[key]['accrual_amount'] = int(r['total_amount'])
                    else:
                        all_accounts[key] = {
                            'account_id': key,
                            'account_code': r['account_code'],
                            'account_name': r['account_name'],
                            'cash_amount': 0,
                            'accrual_amount': int(r['total_amount'])
                        }

                lines = []
                for acc in all_accounts.values():
                    acc['difference'] = acc['accrual_amount'] - acc['cash_amount']
                    lines.append(ComparisonLine(**acc))

                cash_total = sum(a['cash_amount'] for a in all_accounts.values())
                accrual_total = sum(a['accrual_amount'] for a in all_accounts.values())

                return ComparisonSection(
                    accounts=lines,
                    cash_total=cash_total,
                    accrual_total=accrual_total,
                    difference=accrual_total - cash_total
                )

            revenue_comparison = build_comparison(cash_revenue, accrual_revenue)

            # Separate expenses by type for both bases
            async def categorize_expenses(expense_rows):
                cogs = []
                operating = []
                other = []
                for r in expense_rows:
                    account_type = await conn.fetchval("""
                        SELECT account_type FROM chart_of_accounts WHERE id = $1
                    """, r['account_id'])
                    if account_type == 'COGS':
                        cogs.append(r)
                    elif account_type == 'OTHER_EXPENSE':
                        other.append(r)
                    else:
                        operating.append(r)
                return cogs, operating, other

            cash_cogs, cash_operating, cash_other = await categorize_expenses(cash_expenses)
            accrual_cogs, accrual_operating, accrual_other = await categorize_expenses(accrual_expenses)

            cogs_comparison = build_comparison(cash_cogs, accrual_cogs)
            operating_comparison = build_comparison(cash_operating, accrual_operating)
            other_expense_comparison = build_comparison(cash_other, accrual_other)

            # Other income (same for both bases - no timing difference for interest/forex)
            other_income_rows = await conn.fetch("""
                SELECT
                    jl.account_id,
                    coa.account_code,
                    coa.account_name,
                    SUM(jl.credit - jl.debit)::BIGINT as total_amount
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                AND je.entry_date BETWEEN $2 AND $3
                AND coa.account_type = 'OTHER_INCOME'
                GROUP BY jl.account_id, coa.account_code, coa.account_name
                HAVING SUM(jl.credit - jl.debit) != 0
            """, tenant_id, start_date.date(), end_date.date())

            other_income_comparison = build_comparison(other_income_rows, other_income_rows)

            # Calculate final numbers
            gross_profit_cash = revenue_comparison.cash_total - cogs_comparison.cash_total
            gross_profit_accrual = revenue_comparison.accrual_total - cogs_comparison.accrual_total

            operating_income_cash = gross_profit_cash - operating_comparison.cash_total
            operating_income_accrual = gross_profit_accrual - operating_comparison.accrual_total

            net_income_cash = operating_income_cash + other_income_comparison.cash_total - other_expense_comparison.cash_total
            net_income_accrual = operating_income_accrual + other_income_comparison.accrual_total - other_expense_comparison.accrual_total

            logger.info(f"Comparison report generated: tenant={tenant_id}, periode={periode}")

            return ComparisonReportResponse(
                success=True,
                data=CashAccrualComparisonReport(
                    period=periode,
                    revenue=revenue_comparison,
                    cost_of_goods_sold=cogs_comparison,
                    gross_profit_cash=gross_profit_cash,
                    gross_profit_accrual=gross_profit_accrual,
                    gross_profit_difference=gross_profit_accrual - gross_profit_cash,
                    operating_expenses=operating_comparison,
                    operating_income_cash=operating_income_cash,
                    operating_income_accrual=operating_income_accrual,
                    operating_income_difference=operating_income_accrual - operating_income_cash,
                    other_income=other_income_comparison,
                    other_expenses=other_expense_comparison,
                    net_income_cash=net_income_cash,
                    net_income_accrual=net_income_accrual,
                    net_income_difference=net_income_accrual - net_income_cash
                )
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get comparison report error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate comparison report")


# ========================================
# Timing Differences Report
# ========================================

@router.get("/timing-differences", response_model=TimingDifferencesResponse)
async def get_timing_differences(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """
    Get report of timing differences between cash and accrual basis.

    Shows unpaid invoices (revenue recognized in accrual but not cash)
    and unpaid bills (expenses recognized in accrual but not cash).
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        conn = await get_db_connection()

        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)

            # Get unpaid invoices
            unpaid_invoices = await conn.fetch("""
                SELECT
                    si.id,
                    si.invoice_number,
                    c.name as customer_name,
                    si.invoice_date,
                    si.due_date,
                    si.total_amount,
                    si.amount_paid as paid_amount,
                    (si.total_amount - si.amount_paid) as balance_due
                FROM sales_invoices si
                LEFT JOIN customers c ON c.id = si.customer_id
                WHERE si.tenant_id = $1
                AND si.status IN ('sent', 'partial', 'overdue')
                AND si.invoice_date <= $2
                ORDER BY si.invoice_date
            """, tenant_id, as_of_date)

            unpaid_invoice_items = [
                UnpaidInvoiceItem(
                    id=str(r['id']),
                    invoice_number=r['invoice_number'],
                    customer_name=r['customer_name'] or 'Unknown',
                    invoice_date=r['invoice_date'].isoformat() if r['invoice_date'] else '',
                    due_date=r['due_date'].isoformat() if r['due_date'] else None,
                    total_amount=int(r['total_amount'] or 0),
                    paid_amount=int(r['paid_amount'] or 0),
                    balance_due=int(r['balance_due'] or 0)
                ) for r in unpaid_invoices
            ]
            total_unpaid_revenue = sum(int(r['balance_due'] or 0) for r in unpaid_invoices)

            # Get unpaid bills
            unpaid_bills = await conn.fetch("""
                SELECT
                    b.id,
                    b.bill_number,
                    v.name as vendor_name,
                    b.bill_date,
                    b.due_date,
                    b.grand_total as total_amount,
                    b.amount_paid as paid_amount,
                    (b.grand_total - b.amount_paid) as balance_due
                FROM bills b
                LEFT JOIN vendors v ON v.id = b.vendor_id
                WHERE b.tenant_id = $1
                AND b.status IN ('approved', 'partial', 'overdue')
                AND b.bill_date <= $2
                ORDER BY b.bill_date
            """, tenant_id, as_of_date)

            unpaid_bill_items = [
                UnpaidBillItem(
                    id=str(r['id']),
                    bill_number=r['bill_number'],
                    vendor_name=r['vendor_name'] or 'Unknown',
                    bill_date=r['bill_date'].isoformat() if r['bill_date'] else '',
                    due_date=r['due_date'].isoformat() if r['due_date'] else None,
                    total_amount=int(r['total_amount'] or 0),
                    paid_amount=int(r['paid_amount'] or 0),
                    balance_due=int(r['balance_due'] or 0)
                ) for r in unpaid_bills
            ]
            total_unpaid_expenses = sum(int(r['balance_due'] or 0) for r in unpaid_bills)

            net_timing_difference = total_unpaid_revenue - total_unpaid_expenses

            logger.info(f"Timing differences report: tenant={tenant_id}, as_of={as_of_date}")

            return TimingDifferencesResponse(
                success=True,
                data=TimingDifferencesReport(
                    as_of_date=as_of_date.isoformat(),
                    unpaid_invoices=unpaid_invoice_items,
                    total_unpaid_revenue=total_unpaid_revenue,
                    unpaid_bills=unpaid_bill_items,
                    total_unpaid_expenses=total_unpaid_expenses,
                    net_timing_difference=net_timing_difference
                )
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get timing differences error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate timing differences report")


# ========================================
# AR/AP Aging Reports
# ========================================

from ..schemas.aging_reports import (
    ARAgingSummary,
    ARAgingSummaryResponse,
    ARAgingDetailItem,
    ARAgingDetailResponse,
    ARCustomerAgingItem,
    ARCustomerAgingResponse,
    APAgingSummary,
    APAgingSummaryResponse,
    APAgingDetailItem,
    APAgingDetailResponse,
    APVendorAgingItem,
    APVendorAgingResponse,
    AgingBracketsResponse,
    AgingBracketsUpdate,
    AgingSnapshotResponse,
    AgingSnapshotListResponse,
    CreateSnapshotRequest,
    CreateSnapshotResponse,
    AgingTrendItem,
    AgingTrendResponse,
    AgingType,
)


@router.get("/ar-aging", response_model=ARAgingSummaryResponse)
async def get_ar_aging_summary(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """
    Get AR (Accounts Receivable) aging summary report.

    Shows total receivables broken down by aging brackets:
    - Current (not yet due)
    - 1-30 days overdue
    - 31-60 days overdue
    - 61-90 days overdue
    - 91-120 days overdue
    - 120+ days overdue
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            row = await conn.fetchrow(
                "SELECT * FROM get_ar_aging_summary($1, $2)",
                tenant_id, as_of_date
            )

            summary = ARAgingSummary(
                total_current=row["total_current"],
                total_1_30=row["total_1_30"],
                total_31_60=row["total_31_60"],
                total_61_90=row["total_61_90"],
                total_91_120=row["total_91_120"],
                total_over_120=row["total_over_120"],
                grand_total=row["grand_total"],
                overdue_count=row["overdue_count"],
            )

            return ARAgingSummaryResponse(
                as_of_date=as_of_date,
                summary=summary,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get AR aging summary error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AR aging summary")


@router.get("/ar-aging/detail", response_model=ARAgingDetailResponse)
async def get_ar_aging_detail(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """Get AR aging detail by customer."""
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            rows = await conn.fetch(
                "SELECT * FROM get_ar_aging_detail($1, $2)",
                tenant_id, as_of_date
            )

            items = [ARAgingDetailItem(
                customer_id=row["customer_id"],
                customer_name=row["customer_name"],
                customer_code=row["customer_code"],
                current_amount=row["current_amount"],
                days_1_30=row["days_1_30"],
                days_31_60=row["days_31_60"],
                days_61_90=row["days_61_90"],
                days_91_120=row["days_91_120"],
                days_over_120=row["days_over_120"],
                total_balance=row["total_balance"],
                oldest_invoice_date=row["oldest_invoice_date"],
                invoice_count=row["invoice_count"],
            ) for row in rows]

            summary_row = await conn.fetchrow(
                "SELECT * FROM get_ar_aging_summary($1, $2)",
                tenant_id, as_of_date
            )

            summary = ARAgingSummary(
                total_current=summary_row["total_current"],
                total_1_30=summary_row["total_1_30"],
                total_31_60=summary_row["total_31_60"],
                total_61_90=summary_row["total_61_90"],
                total_91_120=summary_row["total_91_120"],
                total_over_120=summary_row["total_over_120"],
                grand_total=summary_row["grand_total"],
                overdue_count=summary_row["overdue_count"],
            )

            return ARAgingDetailResponse(
                as_of_date=as_of_date,
                items=items,
                summary=summary,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get AR aging detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AR aging detail")


@router.get("/ar-aging/customer/{customer_id}", response_model=ARCustomerAgingResponse)
async def get_ar_aging_for_customer(
    request: Request,
    customer_id: uuid.UUID,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """Get AR aging for a single customer."""
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            customer = await conn.fetchrow(
                "SELECT id, name FROM customers WHERE id = $1 AND tenant_id = $2",
                customer_id, tenant_id
            )
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")

            rows = await conn.fetch(
                "SELECT * FROM get_ar_aging_customer($1, $2)",
                customer_id, as_of_date
            )

            items = [ARCustomerAgingItem(
                invoice_id=row["invoice_id"],
                invoice_number=row["invoice_number"],
                invoice_date=row["invoice_date"],
                due_date=row["due_date"],
                total_amount=row["total_amount"],
                paid_amount=row["paid_amount"],
                balance=row["balance"],
                days_overdue=row["days_overdue"],
                aging_bucket=row["aging_bucket"],
            ) for row in rows]

            return ARCustomerAgingResponse(
                customer_id=customer_id,
                customer_name=customer["name"],
                as_of_date=as_of_date,
                items=items,
                total_balance=sum(i.balance for i in items),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get AR aging for customer error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AR aging for customer")


@router.get("/ap-aging", response_model=APAgingSummaryResponse)
async def get_ap_aging_summary(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """
    Get AP (Accounts Payable) aging summary report.

    Shows total payables broken down by aging brackets.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            row = await conn.fetchrow(
                "SELECT * FROM get_ap_aging_summary($1, $2)",
                tenant_id, as_of_date
            )

            summary = APAgingSummary(
                total_current=row["total_current"],
                total_1_30=row["total_1_30"],
                total_31_60=row["total_31_60"],
                total_61_90=row["total_61_90"],
                total_91_120=row["total_91_120"],
                total_over_120=row["total_over_120"],
                grand_total=row["grand_total"],
                overdue_count=row["overdue_count"],
            )

            return APAgingSummaryResponse(
                as_of_date=as_of_date,
                summary=summary,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get AP aging summary error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AP aging summary")


@router.get("/ap-aging/detail", response_model=APAgingDetailResponse)
async def get_ap_aging_detail(
    request: Request,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """Get AP aging detail by vendor."""
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            rows = await conn.fetch(
                "SELECT * FROM get_ap_aging_detail($1, $2)",
                tenant_id, as_of_date
            )

            items = [APAgingDetailItem(
                vendor_id=row["vendor_id"],
                vendor_name=row["vendor_name"],
                vendor_code=row["vendor_code"],
                current_amount=row["current_amount"],
                days_1_30=row["days_1_30"],
                days_31_60=row["days_31_60"],
                days_61_90=row["days_61_90"],
                days_91_120=row["days_91_120"],
                days_over_120=row["days_over_120"],
                total_balance=row["total_balance"],
                oldest_bill_date=row["oldest_bill_date"],
                bill_count=row["bill_count"],
            ) for row in rows]

            summary_row = await conn.fetchrow(
                "SELECT * FROM get_ap_aging_summary($1, $2)",
                tenant_id, as_of_date
            )

            summary = APAgingSummary(
                total_current=summary_row["total_current"],
                total_1_30=summary_row["total_1_30"],
                total_31_60=summary_row["total_31_60"],
                total_61_90=summary_row["total_61_90"],
                total_91_120=summary_row["total_91_120"],
                total_over_120=summary_row["total_over_120"],
                grand_total=summary_row["grand_total"],
                overdue_count=summary_row["overdue_count"],
            )

            return APAgingDetailResponse(
                as_of_date=as_of_date,
                items=items,
                summary=summary,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get AP aging detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AP aging detail")


@router.get("/ap-aging/vendor/{vendor_id}", response_model=APVendorAgingResponse)
async def get_ap_aging_for_vendor(
    request: Request,
    vendor_id: uuid.UUID,
    as_of: Optional[date] = Query(default=None, description="As of date (default: today)")
):
    """Get AP aging for a single vendor."""
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = as_of or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            vendor = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id, tenant_id
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")

            rows = await conn.fetch(
                "SELECT * FROM get_ap_aging_vendor($1, $2)",
                vendor_id, as_of_date
            )

            items = [APVendorAgingItem(
                bill_id=row["bill_id"],
                bill_number=row["bill_number"],
                bill_date=row["bill_date"],
                due_date=row["due_date"],
                total_amount=row["total_amount"],
                paid_amount=row["paid_amount"],
                balance=row["balance"],
                days_overdue=row["days_overdue"],
                aging_bucket=row["aging_bucket"],
            ) for row in rows]

            return APVendorAgingResponse(
                vendor_id=vendor_id,
                vendor_name=vendor["name"],
                as_of_date=as_of_date,
                items=items,
                total_balance=sum(i.balance for i in items),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get AP aging for vendor error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AP aging for vendor")


@router.post("/aging-snapshot", response_model=CreateSnapshotResponse)
async def create_aging_snapshot(
    request: Request,
    data: CreateSnapshotRequest
):
    """Create an aging snapshot for trend analysis."""
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = data.as_of_date or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            if data.snapshot_type == AgingType.ar:
                snapshot_id = await conn.fetchval(
                    "SELECT create_ar_aging_snapshot($1, $2)",
                    tenant_id, as_of_date
                )
            else:
                snapshot_id = await conn.fetchval(
                    "SELECT create_ap_aging_snapshot($1, $2)",
                    tenant_id, as_of_date
                )

            return CreateSnapshotResponse(
                snapshot_id=snapshot_id,
                snapshot_type=data.snapshot_type,
                as_of_date=as_of_date,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create aging snapshot error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create aging snapshot")


@router.get("/aging-trend", response_model=AgingTrendResponse)
async def get_aging_trend(
    request: Request,
    snapshot_type: AgingType = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Get aging trend from snapshots."""
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            rows = await conn.fetch(
                "SELECT * FROM get_aging_trend($1, $2, $3, $4)",
                tenant_id, snapshot_type.value, start_date, end_date
            )

            items = [AgingTrendItem(
                snapshot_date=row["snapshot_date"],
                total_current=row["total_current"],
                total_overdue=row["total_overdue"],
                grand_total=row["grand_total"],
            ) for row in rows]

            return AgingTrendResponse(
                snapshot_type=snapshot_type,
                start_date=start_date,
                end_date=end_date,
                items=items,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get aging trend error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get aging trend")


# ========================================
# Drill-Down Report
# ========================================

from ..schemas.drill_down import (
    DrillDownTransaction,
    DrillDownResponse,
)


@router.get("/drill-down", response_model=DrillDownResponse)
async def get_drill_down(
    request: Request,
    account_id: uuid.UUID = Query(..., description="Account ID to drill into"),
    start_date: date = Query(..., description="Start of period"),
    end_date: date = Query(..., description="End of period"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=50, ge=1, le=200, description="Items per page"),
):
    """
    Get transaction details for a specific account.

    Use this to drill down from P&L or Balance Sheet line items
    to see the underlying journal entries.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            # Get account info
            account = await conn.fetchrow("""
                SELECT id, code, name, type, normal_balance
                FROM chart_of_accounts
                WHERE id = $1 AND tenant_id = $2
            """, account_id, tenant_id)

            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Calculate opening balance (before start_date)
            opening_row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                WHERE jl.account_id = $1
                  AND je.tenant_id = $2
                  AND je.entry_date < $3
                  AND je.status = 'POSTED'
            """, account_id, tenant_id, start_date)

            if account['normal_balance'] == 'DEBIT':
                opening_balance = (opening_row['total_debit'] or 0) - (opening_row['total_credit'] or 0)
            else:
                opening_balance = (opening_row['total_credit'] or 0) - (opening_row['total_debit'] or 0)

            # Get total count for pagination
            total_count = await conn.fetchval("""
                SELECT COUNT(*)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                WHERE jl.account_id = $1
                  AND je.tenant_id = $2
                  AND je.entry_date BETWEEN $3 AND $4
                  AND je.status = 'POSTED'
            """, account_id, tenant_id, start_date, end_date)

            # Get transactions with pagination
            offset = (page - 1) * limit
            rows = await conn.fetch("""
                SELECT
                    je.id as journal_id,
                    je.journal_number,
                    je.entry_date,
                    je.source_type,
                    je.source_id,
                    je.description,
                    jl.memo,
                    jl.debit,
                    jl.credit
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_entry_id
                WHERE jl.account_id = $1
                  AND je.tenant_id = $2
                  AND je.entry_date BETWEEN $3 AND $4
                  AND je.status = 'POSTED'
                ORDER BY je.entry_date, je.created_at
                OFFSET $5 LIMIT $6
            """, account_id, tenant_id, start_date, end_date, offset, limit)

            # Calculate running balance and build transactions
            transactions = []
            running_balance = opening_balance
            total_debit = 0
            total_credit = 0

            for row in rows:
                debit = row['debit'] or 0
                credit = row['credit'] or 0
                total_debit += debit
                total_credit += credit

                if account['normal_balance'] == 'DEBIT':
                    running_balance = running_balance + debit - credit
                else:
                    running_balance = running_balance + credit - debit

                transactions.append(DrillDownTransaction(
                    journal_id=row['journal_id'],
                    journal_number=row['journal_number'],
                    entry_date=row['entry_date'],
                    source_type=row['source_type'],
                    source_id=row['source_id'],
                    description=row['description'],
                    memo=row['memo'],
                    debit=debit,
                    credit=credit,
                    running_balance=running_balance,
                ))

            closing_balance = opening_balance
            if account['normal_balance'] == 'DEBIT':
                closing_balance = opening_balance + total_debit - total_credit
            else:
                closing_balance = opening_balance + total_credit - total_debit

            logger.info(f"Drill-down generated: tenant={tenant_id}, account={account['code']}, transactions={len(transactions)}")

            return DrillDownResponse(
                account_id=account['id'],
                account_code=account['code'],
                account_name=account['name'],
                account_type=account['type'],
                normal_balance=account['normal_balance'],
                period_start=start_date,
                period_end=end_date,
                opening_balance=opening_balance,
                total_debit=total_debit,
                total_credit=total_credit,
                closing_balance=closing_balance,
                transactions=transactions,
                pagination={
                    "page": page,
                    "limit": limit,
                    "total": total_count,
                    "has_more": offset + len(transactions) < total_count,
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get drill-down error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate drill-down report")
