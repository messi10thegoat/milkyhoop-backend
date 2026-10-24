"""Pemindai idempotensi per HANDLER lewat AST (syarat MASTER 24 Okt, sesudah positif palsu `close` PO: pemindai teks terkecoh komentar
"X-Idempotency-Key" di fungsi tetangga). Handler dihitung ber-idem HANYA bila TUBUHNYA memanggil penolong idempotensi -- komentar,
docstring, dan string tak dihitung. Daftar WAJIB = handler tulis yang SUDAH dijanjikan idem (kontrak Asisten); celah yang diketahui
dicatat di CELAH (ber-tiket), bukan disembunyikan."""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

WAJIB = {
    "routers/purchase_orders.py": ["create_purchase_order", "update_purchase_order", "delete_purchase_order", "send_purchase_order",
                                   "receive_goods", "convert_to_bill", "cancel_purchase_order", "close_purchase_order"],
    "routers/quotes.py": ["create_quote", "update_quote", "void_quote", "convert_to_invoice"],  # AS-G15 (24 Okt) celah ditutup
    "routers/customers.py": ["create_customer"],
    "routers/items.py": ["create_item"],
    "routers/sj_so.py": ["create_sj_so", "void_sj"],
    "routers/deliveries.py": ["ubah_serah_surat_jalan"],
    "routers/goods_receipts.py": ["void_goods_receipt"],
    "routers/vendor_deposits.py": ["create_vendor_deposit", "update_vendor_deposit", "delete_vendor_deposit", "post_vendor_deposit",
                                   "apply_vendor_deposit", "unapply_vendor_deposit", "refund_vendor_deposit", "void_vendor_deposit"],  # AS-GP4
    # alias (_ib / idem_mulai_aksi) & mekanisme lama -- kontrol positif bahwa alias TERBACA
    "routers/credit_notes.py": ["post_credit_note", "void_credit_note"],
    "routers/receive_payments.py": ["void_receive_payment", "create_receive_payment"],
    "routers/bill_payments.py": ["create_bill_payment"],
    "routers/sales_invoices.py": ["update_invoice", "post_invoice", "void_invoice", "delete_invoice"],
}
# Celah TERUKUR (24 Okt, BACKEND2): penawaran -> faktur tanpa kunci/FOR UPDATE/idem -- tiket ke MASTER. Pemindai MEMASTIKAN masih tercatat
# sebagai celah (bila diperbaiki, pindahkan ke WAJIB -- tes ini merah supaya daftar tak basi).
CELAH = {}  # AS-G15: convert_to_invoice pindah ke WAJIB


FUNGSI_IDEM = {"mulai", "mulai_aksi"}          # services/idem_buat
PANGGILAN_LAIN = {"execute_idempotent"}       # utils/idempotency (mekanisme lama, tetap idem)


def _alias(pohon) -> tuple:
    """(nama modul idem_buat di berkas ini, {nama lokal fungsi idem}) -- `import idem_buat as _ib`, `from ..services.idem_buat import
    mulai_aksi as idem_mulai_aksi` ikut terhitung (pemindai teks & AST lama melewatkan alias -> negatif palsu)."""
    modul, fungsi = {"idem_buat"}, set(PANGGILAN_LAIN)
    for n in ast.walk(pohon):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "idem_buat":
                    modul.add(a.asname or a.name)
                elif (n.module or "").endswith("idem_buat") and a.name in FUNGSI_IDEM:
                    fungsi.add(a.asname or a.name)
                elif a.name in PANGGILAN_LAIN:
                    fungsi.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.endswith("idem_buat"):
                    modul.add(a.asname or a.name.split(".")[-1])
    return modul, fungsi


def handler_ber_idem(sumber: str, nama: str) -> bool:
    """True bila fungsi `nama` (async/def) di `sumber` MEMANGGIL penolong idem (AST, bukan teks): <alias idem_buat>.mulai/mulai_aksi,
    fungsi idem yang diimpor dengan alias, atau execute_idempotent. Komentar/docstring/string TAK dihitung."""
    pohon = ast.parse(sumber)
    modul, fungsi = _alias(pohon)
    for n in ast.walk(pohon):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nama:
            for c in ast.walk(n):
                if not isinstance(c, ast.Call):
                    continue
                f = c.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in modul and f.attr in FUNGSI_IDEM:
                    return True
                if isinstance(f, ast.Name) and f.id in fungsi:
                    return True
            return False
    raise LookupError(f"handler {nama} tak ditemukan")


@pytest.mark.parametrize("berkas,nama", [(b, n) for b, ns in WAJIB.items() for n in ns])
def test_handler_wajib_memanggil_idem(berkas, nama):
    assert handler_ber_idem((APP / berkas).read_text(), nama), f"{berkas}:{nama} tak memanggil idem_buat.mulai/mulai_aksi"


@pytest.mark.parametrize("berkas,nama", [(b, n) for b, ns in CELAH.items() for n in ns])
def test_celah_tercatat_masih_celah(berkas, nama):
    assert not handler_ber_idem((APP / berkas).read_text(), nama), f"{berkas}:{nama} sudah ber-idem -- pindahkan ke WAJIB"


KOMENTAR_SAJA = '''
async def tutup(request):
    """Docstring menyebut idem_buat.mulai_aksi dan X-Idempotency-Key."""
    # X-Idempotency-Key: idem_buat.mulai_aksi(conn, ...) -- hanya komentar
    teks = "idem_buat.mulai_aksi(conn, ctx, kunci)"
    return teks


async def tetangga(request):
    return await idem_buat.mulai_aksi(None, None, None, "X", 1, {})
'''
ALIAS = '''
from ..services import idem_buat as _ib
from ..services.idem_buat import mulai_aksi as idem_mulai_aksi


async def pakai_alias(request):
    return await _ib.mulai_aksi(None, None, None, "X", 1, {})


async def pakai_fungsi(request):
    return await idem_mulai_aksi(None, None, None, "X", 1, {})


async def bukan(request):
    return await _ib.kunci_dari(request)  # membaca kunci saja (didelegasikan) BUKAN idem di handler
'''


def test_kontrol_merah_komentar_dan_string_tak_dihitung():
    assert handler_ber_idem(KOMENTAR_SAJA, "tutup") is False  # pemindai teks lama: True (positif palsu `close`)
    assert handler_ber_idem(KOMENTAR_SAJA, "tetangga") is True  # kontrol positif
    with pytest.raises(LookupError):
        handler_ber_idem(KOMENTAR_SAJA, "tak_ada")


def test_alias_impor_terbaca_dan_baca_kunci_saja_bukan_idem():
    assert handler_ber_idem(ALIAS, "pakai_alias") and handler_ber_idem(ALIAS, "pakai_fungsi")
    assert handler_ber_idem(ALIAS, "bukan") is False
