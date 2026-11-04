"""U-P4c (2 Nov 2026, tiket P4 BE untuk FE): (a) pratinjau terima bayar SO/faktur membawa journals[] + bank_transactions[] dari penentu
YANG SAMA (rencana_penerimaan_ro, sumber /receive-payments/preview); (b) /bill-payments/preview membawa payment_number_preview +
payment_number_estimated. NOMOR-PK (4 Nov 2026): nomor = SATU seri BP- (services/nomor_bayar_keluar) untuk kedua rute + pratinjau;
generator PAY- dihapus."""
import ast
import asyncio
import inspect

from app.routers import bill_payments as BP
from app.services import nomor_bayar_keluar as NB
from app.services import so_pelunasan as SP


class _Terbit:
    """get_next_journal_number tiruan: urutan nomor; `terpakai` = nomor yang sudah dipakai bill_payments_v2."""
    def __init__(self, urut, terpakai=()):
        self.urut, self.terpakai, self.log = list(urut), set(terpakai), []

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        if "get_next_journal_number" in sql:
            assert a[1] == "BP"
            return self.urut.pop(0)
        if "FROM bill_payments_v2 WHERE tenant_id = $1 AND payment_number = $2" in sql:
            return 1 if a[1] in self.terpakai else None
        raise AssertionError(sql)


def test_nomor_pk_seri_bp_dan_lompati_terpakai():
    # NOMOR-PK (4 Nov 2026): SATU seri BP- untuk /bill-payments dan /bills/{id}/payments; nomor yang sudah dipakai dilompati
    assert asyncio.run(NB.nomor_pk(_Terbit(["BP-2611-0003"]), "t")) == "BP-2611-0003"
    k = _Terbit(["BP-2611-0003", "BP-2611-0004"], terpakai={"BP-2611-0003"})
    assert asyncio.run(NB.nomor_pk(k, "t")) == "BP-2611-0004"


def _panggil(fn, nama):
    pohon = ast.parse(inspect.getsource(fn).lstrip())
    return [n for n in ast.walk(pohon) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(nama)]


def test_dua_rute_dan_pratinjau_satu_penentu_nomor():
    from app.routers import bills as BR
    from app.services import bills_service as BS
    assert _panggil(BP.create_bill_payment, "nomor_pk") and _panggil(BS.BillsService.record_payment, "nomor_pk")
    for fn in (BP.preview_create_bill_payment, BR.preview_record_payment):
        assert _panggil(fn, "perkiraan_nomor_pk"), fn.__name__
    s = inspect.getsource(BP.preview_create_bill_payment)
    assert s.index("transaction(readonly=True)") < s.index("perkiraan_nomor_pk(") < s.index("tr.rollback()")
    assert '"payment_number_preview": nomor, "payment_number_estimated": True' in s
    for nama in ("generate_payment_number", "perkiraan_nomor_bayar", "format_nomor_bayar"):  # generator PAY- DIHAPUS
        assert not hasattr(BP, nama), nama
    assert "bill_payment_sequences" not in inspect.getsource(BP)


def test_jurnal_bank_dari_penentu_yang_sama():
    assert SP._jurnal_bank(None) == {"journals": [], "bank_transactions": []}
    j, b = [{"journal_number": None, "lines": []}], [{"bank_account": "BCA", "amount": 1.0}]
    d = SP._jurnal_bank({"journals": j, "bank_transactions": b})
    assert d["journals"] is j and d["bank_transactions"] is b
    s = inspect.getsource(SP._jalankan_inti)
    assert '"journals": rr["journals"], "bank_transactions": rr["bank_transactions"]' in s
    for fn in (SP.pratinjau_pelunasan_so, SP.pratinjau_pelunasan_faktur):
        assert "**_jurnal_bank(hasil)," in inspect.getsource(fn), fn.__name__
