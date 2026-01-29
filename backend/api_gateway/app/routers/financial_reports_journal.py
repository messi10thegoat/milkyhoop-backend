"""
Iron Laws Compliant Financial Reports - Journal-Based Implementations

These endpoints derive all financial data from journal_entries (Law 1 compliance).
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import logging
import asyncpg

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports-journal"])


class AsetLancarResponse(BaseModel):
    kas: int = 0
    bank: int = 0
    persediaan: int = 0
    piutang_usaha: int = 0
    beban_dibayar_dimuka: int = 0
    uang_muka_pembelian: int = 0
    total: int = 0


class AsetTetapResponse(BaseModel):
    peralatan: int = 0
    akum_penyusutan_peralatan: int = 0
    kendaraan: int = 0
    akum_penyusutan_kendaraan: int = 0
    bangunan: int = 0
    akum_penyusutan_bangunan: int = 0
    tanah: int = 0
    total_bruto: int = 0
    total_akum_penyusutan: int = 0
    total_neto: int = 0


class KewajibanJangkaPendekResponse(BaseModel):
    hutang_usaha: int = 0
    hutang_bank_jangka_pendek: int = 0
    uang_muka_pelanggan: int = 0
    total: int = 0


class KewajibanJangkaPanjangResponse(BaseModel):
    hutang_bank_jangka_panjang: int = 0
    total: int = 0


class EkuitasResponse(BaseModel):
    modal: int = 0
    laba_ditahan: int = 0
    prive: int = 0
    total: int = 0


class NeracaJournalResponse(BaseModel):
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
    source: str = "journal_entries"


async def get_db_connection():
    """Get database connection using centralized config."""
    from ..config import settings
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


def parse_periode(periode: str):
    """Parse period string into start and end dates."""
    from datetime import datetime
    import calendar
    
    if "-Q" in periode:
        year, quarter = periode.split("-Q")
        quarter = int(quarter)
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        start_date = datetime(int(year), start_month, 1)
        last_day = calendar.monthrange(int(year), end_month)[1]
        end_date = datetime(int(year), end_month, last_day, 23, 59, 59)
    elif len(periode) == 7:
        year, month = periode.split("-")
        start_date = datetime(int(year), int(month), 1)
        last_day = calendar.monthrange(int(year), int(month))[1]
        end_date = datetime(int(year), int(month), last_day, 23, 59, 59)
    else:
        year = int(periode)
        start_date = datetime(year, 1, 1)
        end_date = datetime(year, 12, 31, 23, 59, 59)
    
    return start_date, end_date


@router.get("/neraca-journal/{periode}", response_model=NeracaJournalResponse)
async def get_neraca_journal(request: Request, periode: str):
    """
    Get Neraca (Balance Sheet) using journal entries - Iron Laws Law 1 compliant.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = str(request.state.user.get("tenant_id"))
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date = parse_periode(periode)

        conn = await get_db_connection()
        try:
            query = """
                SELECT
                    coa.account_code,
                    coa.name,
                    coa.account_type,
                    CASE 
                        WHEN coa.normal_balance = 'DEBIT' THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
                        ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
                    END as balance
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.tenant_id = $1
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE coa.tenant_id = $1
                GROUP BY coa.id, coa.account_code, coa.name, coa.account_type, coa.normal_balance
                ORDER BY coa.account_code
            """
            rows = await conn.fetch(query, tenant_id, end_date.date())

            kas = bank = piutang_usaha = persediaan = 0
            beban_dibayar_dimuka = uang_muka_pembelian = 0
            peralatan = kendaraan = bangunan = tanah = 0
            akum_peny_peralatan = akum_peny_kendaraan = akum_peny_bangunan = 0
            hutang_usaha = hutang_bank_jk_pendek = uang_muka_pelanggan = 0
            hutang_bank_jk_panjang = 0
            modal = laba_ditahan = prive = 0

            for r in rows:
                code = r["account_code"] or ""
                name = (r["name"] or "").lower()
                balance = int(r["balance"]) if r["balance"] else 0

                if code.startswith("1-10"):
                    if "kas" in name:
                        kas += balance
                    elif "bank" in name:
                        bank += balance
                    elif "piutang" in name:
                        piutang_usaha += balance
                    elif "persediaan" in name:
                        persediaan += balance
                    elif "dibayar" in name:
                        beban_dibayar_dimuka += balance
                    elif "uang muka" in name:
                        uang_muka_pembelian += balance

                elif code.startswith("1-2"):
                    if "tanah" in name:
                        tanah += balance
                    elif "bangunan" in name and "akum" not in name:
                        bangunan += balance
                    elif "kendaraan" in name and "akum" not in name:
                        kendaraan += balance
                    elif "peralatan" in name and "akum" not in name:
                        peralatan += balance
                    elif "akum" in name:
                        if "peralatan" in name:
                            akum_peny_peralatan += abs(balance)
                        elif "kendaraan" in name:
                            akum_peny_kendaraan += abs(balance)
                        elif "bangunan" in name:
                            akum_peny_bangunan += abs(balance)

                elif code.startswith("2-1"):
                    if "hutang usaha" in name or "payable" in name:
                        hutang_usaha += balance
                    elif "bank" in name or "cc" in name:
                        hutang_bank_jk_pendek += balance
                    elif "uang muka" in name:
                        uang_muka_pelanggan += balance
                    else:
                        hutang_usaha += balance

                elif code.startswith("2-2"):
                    hutang_bank_jk_panjang += balance

                elif code.startswith("3"):
                    if "modal" in name:
                        modal += balance
                    elif "laba" in name:
                        laba_ditahan += balance
                    elif "prive" in name:
                        prive += abs(balance)
                    else:
                        modal += balance

            total_aset_lancar = kas + bank + persediaan + piutang_usaha + beban_dibayar_dimuka + uang_muka_pembelian
            total_aset_tetap_bruto = peralatan + kendaraan + bangunan + tanah
            total_akum_penyusutan = akum_peny_peralatan + akum_peny_kendaraan + akum_peny_bangunan
            total_aset_tetap_neto = total_aset_tetap_bruto - total_akum_penyusutan
            total_aset = total_aset_lancar + total_aset_tetap_neto

            total_kewajiban_jk_pendek = hutang_usaha + hutang_bank_jk_pendek + uang_muka_pelanggan
            total_kewajiban_jk_panjang = hutang_bank_jk_panjang
            total_kewajiban = total_kewajiban_jk_pendek + total_kewajiban_jk_panjang

            total_ekuitas = modal + laba_ditahan - prive

            return NeracaJournalResponse(
                periode=periode,
                tanggal=end_date.strftime("%d %B %Y"),
                aset_lancar=AsetLancarResponse(
                    kas=kas, bank=bank, persediaan=persediaan,
                    piutang_usaha=piutang_usaha, beban_dibayar_dimuka=beban_dibayar_dimuka,
                    uang_muka_pembelian=uang_muka_pembelian, total=total_aset_lancar
                ),
                aset_tetap=AsetTetapResponse(
                    peralatan=peralatan, akum_penyusutan_peralatan=akum_peny_peralatan,
                    kendaraan=kendaraan, akum_penyusutan_kendaraan=akum_peny_kendaraan,
                    bangunan=bangunan, akum_penyusutan_bangunan=akum_peny_bangunan,
                    tanah=tanah, total_bruto=total_aset_tetap_bruto,
                    total_akum_penyusutan=total_akum_penyusutan, total_neto=total_aset_tetap_neto
                ),
                total_aset=total_aset,
                kewajiban_jangka_pendek=KewajibanJangkaPendekResponse(
                    hutang_usaha=hutang_usaha, hutang_bank_jangka_pendek=hutang_bank_jk_pendek,
                    uang_muka_pelanggan=uang_muka_pelanggan, total=total_kewajiban_jk_pendek
                ),
                kewajiban_jangka_panjang=KewajibanJangkaPanjangResponse(
                    hutang_bank_jangka_panjang=hutang_bank_jk_panjang, total=total_kewajiban_jk_panjang
                ),
                total_kewajiban=total_kewajiban,
                ekuitas=EkuitasResponse(
                    modal=modal, laba_ditahan=laba_ditahan, prive=prive, total=total_ekuitas
                ),
                is_balanced=(abs(total_aset - (total_kewajiban + total_ekuitas)) < 100),
                source="journal_entries"
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get neraca journal error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate neraca report: {str(e)}")
