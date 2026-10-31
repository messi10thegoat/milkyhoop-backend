"""L5b (31 Okt 2026) -- kolom "Dok" daftar Penerimaan Barang: GET /api/goods-receipts membawa items[].documents + doc_count (ADITIF), pola daftar
Pengiriman (deliveries: documents_baris(kelompok) + doc_count dari pembentuk BATCH). Pembentuk SATU: grn_daftar.kelompok_grn_banyak (2 kueri
batch berapa pun barisnya) -- detail kelompok_grn mendelegasikan kepadanya, jadi daftar == detail. Bukti DB nyata (baca saja):
scripts/ops/uji-nyata-grn-kolom-dok.py."""
import asyncio
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.middleware import permission_middleware as pm
from app.routers import goods_receipts as GR
from app.services import grn_daftar as GD

T = "t-uji"
PO1, PO2 = uuid4(), uuid4()


def _gr(po, st="posted"):
    return {"id": uuid4(), "purchase_order_id": po, "status": st, "receipt_number": f"GRN-{uuid4().hex[:4]}", "receipt_date": date(2026, 10, 30),
            "amount": Decimal("1000")}


class _K:
    """Konek boneka: kueri PO -> baris PO; kueri tagihan bertaut -> baris per gr_id. Mencatat kueri."""
    def __init__(s, grs, tagihan):
        s.grs, s.tagihan, s.sql = grs, tagihan, []

    async def fetch(s, sql, *a):
        s.sql.append((sql, a))
        if "FROM purchase_orders" in sql:
            return [{"id": p, "po_number": f"PO-{str(p)[:4]}", "po_date": date(2026, 10, 1), "total_amount": Decimal("5"), "status": "sent",
                     "sent_at": None} for p in a[1]]
        if "goods_receipt_bill_links" in sql:
            return [b for g in a[1] for b in s.tagihan.get(g, [])]
        return []


def _bill(gr_id, nomor):
    return {"gr_id": gr_id, "id": uuid4(), "invoice_number": nomor, "issue_date": date(2026, 10, 2), "amount": Decimal("7"), "keadaan": "terbuka"}


def test_batch_dua_kueri_berapa_pun_baris_dan_bertenant():
    grs = [_gr(PO1), _gr(PO1), _gr(PO2), _gr(None if False else PO2, "void"), _gr(PO1)]
    k = _K(grs, {grs[0]["id"]: [_bill(grs[0]["id"], "PB-1")]})
    out = asyncio.run(GD.kelompok_grn_banyak(k, T, grs))
    assert len(k.sql) == 3 and set(out) == {str(g["id"]) for g in grs}  # L7 (31 Okt): + 1 batch tautan bagikan (tetap konstan)
    (s1, a1), (s2, a2), (s3, a3) = k.sql
    assert "tenant_id = $1 AND id = ANY($2::uuid[])" in s1 and a1[0] == T and sorted(a1[1], key=str) == sorted({PO1, PO2}, key=str)
    assert "b.tenant_id = $1" in s2 and "l.status = 'active'" in s2 and "i.goods_receipt_id = ANY($2::uuid[])" in s2 and a2 == (T, [g["id"] for g in grs])
    pasangan = list(zip(a3[1], a3[2]))
    assert "FROM document_shares WHERE tenant_id = $1" in s3 and a3[0] == T and len(pasangan) == len(set(pasangan)) == 3  # PO1, PO2, PB-1 unik
    assert sorted(a3[1]) == ["bill", "purchase_order", "purchase_order"]  # GRN belum bisa dibagikan -> tak ditanya


def test_detail_sama_dengan_daftar_per_baris():
    grs = [_gr(PO1), _gr(PO2)]
    tag = {grs[1]["id"]: [_bill(grs[1]["id"], "PB-2"), _bill(grs[1]["id"], "PB-3")]}
    banyak = asyncio.run(GD.kelompok_grn_banyak(_K(grs, tag), T, grs))
    for g in grs:
        assert asyncio.run(GD.kelompok_grn(_K(grs, tag), T, g)) == banyak[str(g["id"])]
    assert [x["type"] for x in GD.documents_baris(banyak[str(grs[1]["id"])])] == ["purchase_order", "goods_receipt", "bill", "bill"]
    assert [x["type"] for x in GD.documents_baris(banyak[str(grs[0]["id"])])] == ["purchase_order", "goods_receipt"]


def test_documents_baris_tanpa_rekap():
    kel = [{"key": "rekap", "docs": [{"kind": "rekap"}]}, {"key": "grn", "docs": [{"kind": "goods_receipt"}]}]
    assert GD.documents_baris(kel) == [{"type": "goods_receipt"}]


def test_kosong_tanpa_kueri():
    k = _K([], {})
    assert asyncio.run(GD.kelompok_grn_banyak(k, T, [])) == {} and k.sql == []


class _KL(_K):
    async def fetchval(s, sql, *a):
        s.sql.append((sql, a))
        return len(s.grs)

    async def fetch(s, sql, *a):
        if "FROM goods_receipts g" in sql and "ORDER BY g.receipt_date" in sql:
            s.sql.append((sql, a))
            return s.grs
        return await super().fetch(sql, *a)


class _R(dict):
    def keys(self):
        return super().keys()

    def get(self, k, d=None):
        return super().get(k, d)


def test_daftar_aditif_documents_doc_count_dan_tanpa_n_plus_1(monkeypatch):
    grs = [_R({**_gr(PO1), "vendor_id": None, "warehouse_id": None, "journal_id": None, "reversal_journal_id": None, "voided_at": None,
               "po_number": "PO-x", "vendor_name": "V", "warehouse_name": "W", "notes": None, "voided_reason": None}) for _ in range(4)]
    tag = {grs[0]["id"]: [_bill(grs[0]["id"], "PB-9")]}
    k = _KL(grs, tag)

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *e): return False

    class P:
        def acquire(s): return CM()

    async def gp(): return P()
    monkeypatch.setattr(GR, "get_pool", gp)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uuid4())}), headers={})
    monkeypatch.setattr(GR, "get_user_context", lambda r: {"tenant_id": T, "user_id": uuid4()})
    out = asyncio.run(GR.list_goods_receipts(req, None, None, None, 50, 0))["data"]["items"]
    assert len(k.sql) == 5   # count + baris + 2 batch + 1 batch tautan bagikan L7 (BUKAN 5 + 3N)
    assert out[0]["doc_count"] == 3 and [d["type"] for d in out[0]["documents"]] == ["purchase_order", "goods_receipt", "bill"]
    assert all(o["doc_count"] == len(o["documents"]) for o in out) and out[1]["doc_count"] == 2
    assert "receipt_number" in out[0] and "status" in out[0]   # medan lama tetap (aditif)


def test_izin_daftar_tak_berubah_dan_detail_memakai_pembentuk_sama():
    assert pm.izin_untuk_jalur("/api/goods-receipts") == ("purchase_order", "R")
    assert "GD.kelompok_grn(conn, tid, gr)" in inspect.getsource(GR.get_goods_receipt_documents)
    assert "GD.kelompok_grn_banyak(conn, ctx[\"tenant_id\"], rows)" in inspect.getsource(GR.list_goods_receipts)
