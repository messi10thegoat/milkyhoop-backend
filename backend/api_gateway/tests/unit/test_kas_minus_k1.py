"""K1 kas-negatif (6 Nov 2026; putusan pemilik LANGSUNG + lingkup MASTER, pola Accurate "kas tidak boleh minus" untuk dokumen KAS KELUAR):
bayar tagihan (buat-posted, posting draf, jalur lama /bills/{id}/payments), posting uang muka vendor, refund nota kredit, refund uang muka
pelanggan memakai aturan SATU dari B8 (kasbank_tulis.tolak_kas_minus_coa, kalimat sama). Tulis = kunci advisory per CoA kas SEBELUM saldo
dibaca (juga Uang Keluar/transfer/biaya B8) -> dua kas keluar serentak: yang kedua DITOLAK. Bank boleh minus. Void tidak dikunci."""
import asyncio
import inspect
import os
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import kasbank_tulis as KT  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

KAS, BANK, LAIN = "coa-kas", "coa-bank", "coa-hutang"


class _Buku:
    """Saldo per CoA + kunci advisory transaksi (asyncio.Lock per kunci, dilepas saat 'commit')."""

    def __init__(self, saldo):
        self.saldo, self.kunci, self.urutan = dict(saldo), {}, []

    def conn(self):
        return _Conn(self)


class _Conn:
    def __init__(self, buku):
        self.b, self.dipegang = buku, []

    async def execute(self, sql, *a):
        assert "pg_advisory_xact_lock" in sql
        self.b.urutan.append(a[0])
        lk = self.b.kunci.setdefault(a[0], asyncio.Lock())
        await lk.acquire()
        self.dipegang.append(lk)

    async def fetchrow(self, sql, coa, tid, tgl):
        await asyncio.sleep(0)  # titik serah: pembaca lain bisa menyela di sini
        if coa not in (KAS, BANK):
            return None
        return {"id": coa, "account_name": "Kas Toko" if coa == KAS else "BCA", "account_type": "cash" if coa == KAS else "bank",
                "coa_id": coa, "is_active": True, "saldo": self.b.saldo[coa]}

    def commit(self):
        for lk in self.dipegang:
            lk.release()
        self.dipegang = []


def _jalan(coro):
    return asyncio.run(coro)


def test_kas_minus_ditolak_saat_tulis_kalimat_b8():
    c = _Buku({KAS: D(300), BANK: D(0)}).conn()
    with pytest.raises(GalatBerkode) as e:
        _jalan(KT.tolak_kas_minus_baris(c, "t", [(LAIN, 500, 0, "x"), (KAS, 0, 500, "x")]))
    assert e.value.code == "KAS_MINUS" and e.value.status_code == 400
    assert e.value.detail == ("Saldo Kas Toko sekarang Rp 300, tidak cukup untuk Rp 500; kas tidak bisa minus. "
                              "Bila uang di laci berbeda dengan catatan, hitung fisik kas dulu.")


def test_pratinjau_mengumpulkan_tanpa_kunci_bank_boleh_minus():
    b = _Buku({KAS: D(300), BANK: D(0)})
    c = b.conn()
    blok = _jalan(KT.tolak_kas_minus_baris(c, "t", [(LAIN, 900, 0, "x"), (KAS, 0, 500, "x"), (BANK, 0, 400, "x")], KT.Blok(True)))
    assert [x["code"] for x in blok] == ["KAS_MINUS"] and b.urutan == []


def test_bersih_per_coa_debit_mengurangi_keluar_dan_kunci_berurutan():
    b = _Buku({KAS: D(100), BANK: D(0)})
    c = b.conn()
    _jalan(KT.tolak_kas_minus_baris(c, "t", [(KAS, 0, 150, "bayar"), (KAS, 60, 0, "balik"), (LAIN, 90, 0, "x"), (BANK, 0, 10, "f")]))
    assert b.urutan == ["KAS_KELUAR:t:coa-bank", "KAS_KELUAR:t:coa-kas"]   # 150 - 60 = 90 <= 100 -> lolos; kunci urut CoA


def _dua_serentak(fungsi_cek):
    """Dua kas keluar 200 dari saldo 300, serentak: cek -> (serah) -> tulis jurnal -> commit."""
    b = _Buku({KAS: D(300), BANK: D(0)})
    hasil = []

    async def satu(nama):
        c = b.conn()
        try:
            await fungsi_cek(c)
            await asyncio.sleep(0)
            b.saldo[KAS] -= 200
            hasil.append((nama, "lolos"))
        except GalatBerkode as e:
            hasil.append((nama, e.code))
        finally:
            c.commit()

    async def keduanya():
        await asyncio.gather(satu("A"), satu("B"))
    asyncio.run(keduanya())
    return sorted(hasil), b.saldo[KAS]


def test_dua_kas_keluar_serentak_yang_kedua_ditolak():
    hasil, saldo = _dua_serentak(lambda c: KT.tolak_kas_minus_baris(c, "t", [(LAIN, 200, 0, "x"), (KAS, 0, 200, "x")]))
    assert hasil == [("A", "lolos"), ("B", "KAS_MINUS")] and saldo == D(100)


def test_tanpa_kunci_keduanya_lolos_kontrol_merah():
    # kontrol: alat ukur BISA merah -- tanpa kunci (pratinjau-gaya) dua kas keluar serentak sama-sama lolos -> kas -100
    hasil, saldo = _dua_serentak(lambda c: KT.tolak_kas_minus_baris(c, "t", [(KAS, 0, 200, "x")], KT.Blok(False), kunci=False))
    assert hasil == [("A", "lolos"), ("B", "lolos")] and saldo == D(-100)


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def test_semua_jalur_k1_memakai_aturan_satu():
    from app.routers import bill_payments as BP
    from app.services import bayar_keluar_rencana as BKR, bills_service as BS, uang_muka_vendor_rencana as UMV
    from app.services import nota_kredit_rencana as NKR, uang_muka_rencana as UMR
    assert 'await KT.tolak_kas_minus_baris(conn, ctx["tenant_id"], baris)' in _src(BP.create_bill_payment_journal)
    for fn in (BKR.rencana_bayar, BKR.pratinjau_bayar_tagihan, BKR.pratinjau_posting_draf_bayar):
        assert "await KT.tolak_kas_minus_baris(conn, tid, baris, bl)" in _src(fn), fn.__name__
    assert "await KT.tolak_kas_minus_baris(conn, tenant_id, _baris_bayar)" in _src(BS.BillsService)
    assert "await kas_minus(conn, tid, coa_kas, jumlah, bloks, pratinjau)" in _src(UMV.rencana_buat)
    assert "await kas_minus(conn, tid, coa_kas, jumlah, bloks, pratinjau)" in _src(UMV.rencana_posting)
    assert "kunci=not pratinjau" in _src(UMV.kas_minus)
    assert 'KT.tolak_kas_minus_coa(conn, tid, hasil["akun"], body.amount, KT.Blok(bl.pratinjau))' in _src(NKR.rencana_refund_nk)
    assert "await KT.tolak_kas_minus_coa(conn, tid, akun_kas, body.amount)" in _src(UMR.rencana_kembalikan)


def test_jalur_b8_kunci_sebelum_saldo_dibaca_ulang():
    tx = _src(KT.rencana_tx_manual)
    assert tx.index('await kunci_kas(conn, tid, a["coa_id"])') < tx.index("a = await akun(conn, tid, ba_id)", tx.index("kunci_kas")) < tx.index("_kas_minus(bl, a, jumlah)")
    tf = _src(KT.rencana_posting_transfer)
    assert tf.index('await kunci_kas(conn, tid, t["from_coa_id"])') < tf.index('segar = await akun(conn, tid, t["from_bank_id"])') < tf.index("_kas_minus(bl, asal, total)")
    assert '_d(asal["saldo"]) < total' in tf   # bank sumber: saldo segar, bukan baris pemanggil
    co = _src(KT.tolak_kas_minus_coa)
    assert co.index("await kunci_kas(conn, tid, coa_id)") < co.index("SQL_AKUN_COA")
