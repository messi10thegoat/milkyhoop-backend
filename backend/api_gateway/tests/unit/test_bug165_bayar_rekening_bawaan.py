"""BUG-165: rekening bayar bawaan untuk Bayar Tagihan/Pembayaran Keluar = penentu SERVER (services/rekening_bawaan, SAMA dgn Biaya BUG-055), disampaikan GET /api/bill-payments/summary
`paid_through_bawaan` {id, nama, sumber} | null. 'terakhir' = pembayaran keluar POSTED (bukan voided) ber-rekening AKTIF, tersaring tenant. Perilaku Biaya TAK berubah (dikunci)."""
import asyncio
import inspect
import uuid

from app.routers import bill_payments as BP
from app.routers import expenses as EX
from app.services import rekening_bawaan as RB

A, B, C = (uuid.uuid4() for _ in range(3))


def r(i, nama):
    return {"id": i, "account_name": nama}


class Conn:
    def __init__(self, jawab):
        self.jawab, self.panggil = jawab, []

    async def fetchrow(self, sql, *args):
        self.panggil.append((sql, args))
        return self.jawab.get(sql)


def jalan(jawab, *a):
    c = Conn(jawab)
    return asyncio.run(RB.paid_through_bawaan(c, "t1", *a)), c.panggil


def test_biaya_tak_berubah_semua_sumber():
    # panggilan LAMA (tanpa argumen sumber) dan BARU (sumber Biaya eksplisit) identik di semua cabang
    for jawab in ({RB.SQL_TERAKHIR: r(A, "Kas Kecil"), RB.SQL_UTAMA: r(B, "BCA"), RB.SQL_KAS: r(C, "Kas")},
                  {RB.SQL_UTAMA: r(B, "BCA"), RB.SQL_KAS: r(C, "Kas")}, {RB.SQL_KAS: r(C, "Kas")}, {}):
        assert jalan(jawab) == jalan(jawab, RB.SQL_TERAKHIR)
    h, p = jalan({RB.SQL_TERAKHIR: r(A, "Kas Kecil"), RB.SQL_TERAKHIR_BAYAR: r(B, "BCA")})
    assert h["sumber"] == "terakhir" and h["nama"] == "Kas Kecil" and [s for s, _ in p] == [RB.SQL_TERAKHIR]      # Biaya TIDAK membaca sumber pembayaran keluar
    assert "FROM expenses e" in RB.SQL_TERAKHIR and "e.status = 'posted' AND ba.is_active" in RB.SQL_TERAKHIR
    assert "paid_through_bawaan(conn, ctx[\"tenant_id\"])" in inspect.getsource(EX.get_expenses_summary)


def test_bayar_tagihan_urutan_terakhir_utama_kas_none():
    h, p = jalan({RB.SQL_TERAKHIR_BAYAR: r(A, "BCA"), RB.SQL_UTAMA: r(B, "X")}, RB.SQL_TERAKHIR_BAYAR)
    assert h == {"id": str(A), "nama": "BCA", "sumber": "terakhir"} and [s for s, _ in p] == [RB.SQL_TERAKHIR_BAYAR]
    h, p = jalan({RB.SQL_UTAMA: r(B, "Utama"), RB.SQL_KAS: r(C, "Kas")}, RB.SQL_TERAKHIR_BAYAR)
    assert h["sumber"] == "utama" and [s for s, _ in p] == [RB.SQL_TERAKHIR_BAYAR, RB.SQL_UTAMA]
    h, p = jalan({RB.SQL_KAS: r(C, "Kas")}, RB.SQL_TERAKHIR_BAYAR)
    assert h["sumber"] == "kas" and len(p) == 3
    assert jalan({}, RB.SQL_TERAKHIR_BAYAR)[0] is None


def test_sql_terakhir_bayar_posted_aktif_bertenant():
    q = " ".join(RB.SQL_TERAKHIR_BAYAR.split())
    assert "FROM bill_payments_v2 bp" in q and "bp.tenant_id = $1" in q and "ba.tenant_id = bp.tenant_id" in q          # tenant di kedua tabel
    assert "bp.status = 'posted'" in q and "ba.is_active" in q and "voided" not in q                                     # hanya POSTED + rekening AKTIF
    assert "ORDER BY bp.created_at DESC, bp.id DESC LIMIT 1" in q
    # sumber Biaya tak ikut berubah menjadi sumber bayar (dan sebaliknya)
    assert RB.SQL_TERAKHIR != RB.SQL_TERAKHIR_BAYAR and "expenses" not in RB.SQL_TERAKHIR_BAYAR and "bill_payments_v2" not in RB.SQL_TERAKHIR


def test_summary_pembayaran_keluar_memuat_paid_through_bawaan():
    s = inspect.getsource(BP.get_bill_payments_summary)
    assert "paid_through_bawaan(conn, ctx[\"tenant_id\"], SQL_TERAKHIR_BAYAR)" in s and '"paid_through_bawaan": bawaan' in s
    from app.schemas.bill_payments import BillPaymentSummaryResponse
    assert BillPaymentSummaryResponse.model_fields["data"].annotation is dict                      # kunci baru tak dibuang response_model (pola BUG-097)
