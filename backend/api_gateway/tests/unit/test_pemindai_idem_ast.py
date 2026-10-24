"""Pemindai idempotensi per HANDLER lewat AST (syarat MASTER 24 Okt, sesudah positif palsu `close` PO: pemindai teks terkecoh komentar
"X-Idempotency-Key" di fungsi tetangga). Handler dihitung ber-idem HANYA bila TUBUHNYA memanggil penolong idempotensi -- komentar,
docstring, dan string tak dihitung. Daftar WAJIB = handler tulis yang SUDAH dijanjikan idem (kontrak Asisten); celah yang diketahui
dicatat di CELAH (ber-tiket), bukan disembunyikan."""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
PANGGILAN_IDEM = {("idem_buat", "mulai"), ("idem_buat", "mulai_aksi")}

WAJIB = {
    "routers/purchase_orders.py": ["create_purchase_order", "update_purchase_order", "delete_purchase_order", "send_purchase_order",
                                   "receive_goods", "convert_to_bill", "cancel_purchase_order", "close_purchase_order"],
    "routers/quotes.py": ["create_quote", "update_quote", "void_quote"],
    "routers/customers.py": ["create_customer"],
    "routers/items.py": ["create_item"],
    "routers/sj_so.py": ["create_sj_so", "void_sj"],
    "routers/deliveries.py": ["ubah_serah_surat_jalan"],
    "routers/goods_receipts.py": ["void_goods_receipt"],
}
# Celah TERUKUR (24 Okt, BACKEND2): penawaran -> faktur tanpa kunci/FOR UPDATE/idem -- tiket ke MASTER. Pemindai MEMASTIKAN masih tercatat
# sebagai celah (bila diperbaiki, pindahkan ke WAJIB -- tes ini merah supaya daftar tak basi).
CELAH = {"routers/quotes.py": ["convert_to_invoice"]}


def handler_ber_idem(sumber: str, nama: str) -> bool:
    """True bila fungsi `nama` (async/def) di `sumber` MEMANGGIL idem_buat.mulai / idem_buat.mulai_aksi (AST, bukan teks)."""
    for n in ast.walk(ast.parse(sumber)):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nama:
            for c in ast.walk(n):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) \
                        and (c.func.value.id, c.func.attr) in PANGGILAN_IDEM:
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


def test_kontrol_merah_komentar_dan_string_tak_dihitung():
    assert handler_ber_idem(KOMENTAR_SAJA, "tutup") is False  # pemindai teks lama: True (positif palsu `close`)
    assert handler_ber_idem(KOMENTAR_SAJA, "tetangga") is True  # kontrol positif
    with pytest.raises(LookupError):
        handler_ber_idem(KOMENTAR_SAJA, "tak_ada")
