"""U1e (4 Okt 2026): penawaran membawa SO HASIL konversi (sales_order_id, sales_order_number, order_code, order_title,
order_code_label) di daftar + detail, dan ?search= mencocokkan nomor/kode/judul SO hasil -- aditif (bentuk lama tetap).

Penjaga: (1) konstan per halaman (nol N+1) + tenant eksplisit di SETIAP kueri; (2) hanya converted_to_type='sales_order'
yang membawa SO (konversi ke faktur / belum dikonversi = None, label tetap terisi); (3) pencarian memakai helper SAMA dgn 6
modul lain dengan tenant $1 dan indeks parameter yang sama di kueri COUNT dan DAFTAR; (4) bentuk lama utuh (field lama tetap,
field baru default None)."""
import asyncio
import inspect
import re
import uuid
from datetime import date, datetime, timezone

import pytest

from app.routers import quotes as Q
from app.schemas.quotes import QuoteDetail, QuoteListItem
from app.services import kode_order as KO

T = "t1"
BARU = {"sales_order_id", "sales_order_number", "order_code", "order_title", "order_code_label"}


def _j(c):
    return asyncio.run(c)


class _Conn:
    def __init__(self, pasangan=(), so=(), idnum=(), daftar=(), hitung=0):
        self.pasangan, self.so, self.idnum, self.daftar, self.hitung = pasangan, so, idnum, daftar, hitung
        self.sql, self.args = [], []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql); self.args.append(a)
        return None  # muat_setelan -> BAWAAN (label "Kode order")

    async def fetchval(self, sql, *a):
        self.sql.append(sql); self.args.append(a)
        return self.hitung

    async def fetch(self, sql, *a):
        self.sql.append(sql); self.args.append(a)
        sql = " ".join(sql.split())
        if "SELECT q.id, q.converted_to_id AS so_id" in sql:
            return list(self.pasangan)
        if "FROM sales_orders WHERE" in sql:
            return list(self.so)
        if "JOIN sales_orders so ON so.id = q.converted_to_id" in sql:
            return list(self.idnum)
        return list(self.daftar)


def test_konstan_per_halaman_dan_tenant_eksplisit():
    c1 = _Conn()
    _j(KO.so_hasil_penawaran(c1, T, [uuid.uuid4()]))
    c50 = _Conn()
    _j(KO.so_hasil_penawaran(c50, T, [uuid.uuid4() for _ in range(50)]))
    assert len(c1.sql) == len(c50.sql) <= 5, (len(c1.sql), len(c50.sql))  # nol N+1
    for s, a in zip(c50.sql, c50.args):
        assert "tenant_id" in s and a and a[0] == T, s  # tenant = parameter pertama, eksplisit
    # kueri id+nomor SO: tenant eksplisit di KEDUA sisi join + hanya konversi ke sales_order
    join = next(" ".join(x.split()) for x in c50.sql if "JOIN sales_orders so ON so.id = q.converted_to_id" in " ".join(x.split()))
    assert "so.tenant_id = q.tenant_id" in join and "q.tenant_id = $1" in join and "q.converted_to_type = 'sales_order'" in join
    # id kosong -> tanpa kueri SO sama sekali selain setelan
    assert len(_j(KO.so_hasil_penawaran(_Conn(), T, [])) or {}) == 0


def test_hanya_so_hasil_konversi_membawa_data():
    q_so, q_inv, q_none = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    so_id = uuid.uuid4()
    c = _Conn(pasangan=[{"id": q_so, "so_id": so_id}],
              so=[{"id": so_id, "order_number": "SO-2610-0007", "order_code": "002-10-26", "order_title": "KAOS"}],
              idnum=[{"qid": q_so, "so_id": so_id, "order_number": "SO-2610-0007"}])
    h = _j(KO.so_hasil_penawaran(c, T, [q_so, q_inv, q_none]))
    assert h[str(q_so)] == {"sales_order_id": str(so_id), "sales_order_number": "SO-2610-0007",
                            "order_code": "002-10-26", "order_title": "KAOS", "order_code_label": "Kode order", "order_title_label": "Judul order"}
    kosong = {"sales_order_id": None, "sales_order_number": None, "order_code": None, "order_title": None,
              "order_code_label": "Kode order", "order_title_label": "Judul order"}
    assert h[str(q_inv)] == kosong and h[str(q_none)] == kosong  # konversi ke faktur / belum: None, label terisi


def test_so_tanpa_kode_tetap_membawa_id_dan_nomor():
    q, so_id = uuid.uuid4(), uuid.uuid4()
    c = _Conn(pasangan=[{"id": q, "so_id": so_id}],
              so=[{"id": so_id, "order_number": "SO-9", "order_code": None, "order_title": None}],
              idnum=[{"qid": q, "so_id": so_id, "order_number": "SO-9"}])
    h = _j(KO.so_hasil_penawaran(c, T, [q]))[str(q)]
    assert h["sales_order_id"] == str(so_id) and h["sales_order_number"] == "SO-9" and h["order_code"] is None


def test_sql_sumber_penawaran_hanya_converted_sales_order_dan_bertenant():
    s = " ".join(KO.SUMBER_SO["quote"].split())
    assert "q.tenant_id = $1" in s and "q.converted_to_type = 'sales_order'" in s and "ANY($2::uuid[])" in s
    p = KO.sql_cari_so_induk("quote", "quotes", "$1", "$5")
    assert "quotes.converted_to_type = 'sales_order'" in p and "so_c.tenant_id = $1" in p
    for kol in ("order_number", "order_code", "order_title"):
        assert f"so_c.{kol} ILIKE $5" in p


def _baris_daftar():
    now = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)
    return {"id": uuid.uuid4(), "quote_number": "QUO-1", "quote_date": date(2026, 10, 1), "expiry_date": None,
            "customer_id": uuid.uuid4(), "customer_name": "X", "subject": None, "subtotal": 1, "discount_amount": 0,
            "tax_amount": 0, "total_amount": 1, "dp_amount": None, "dp_percent": None, "status": "converted",
            "converted_to_type": "sales_order", "converted_to_id": uuid.uuid4(), "created_at": now}


def _daftar(monkeypatch, search=None, baris=None, **kw):
    baris = baris if baris is not None else [_baris_daftar()]
    c = _Conn(daftar=baris, hitung=len(baris), **kw)

    async def pool():
        class _P:
            def acquire(self_):
                class _A:
                    async def __aenter__(s):
                        return c

                    async def __aexit__(s, *a):
                        return False
                return _A()
        return _P()

    async def hari(conn, tid):
        return date(2026, 10, 4)
    monkeypatch.setattr(Q, "get_pool", pool)
    monkeypatch.setattr(Q, "tanggal_dokumen", hari)

    class _Req:
        class state:
            user = {"tenant_id": T, "user_id": str(uuid.uuid4())}
    out = _j(Q.list_quotes(_Req(), status="all", customer_id=None, start_date=None, end_date=None, search=search,
                            skip=0, limit=20, page=1, offset=0))
    return c, out


def test_daftar_membawa_field_baru_dan_bentuk_lama_utuh(monkeypatch):
    b = _baris_daftar()
    so_id = b["converted_to_id"]
    c, out = _daftar(monkeypatch, baris=[b], pasangan=[{"id": b["id"], "so_id": so_id}],
                     so=[{"id": so_id, "order_number": "SO-2610-0007", "order_code": "002-10-26", "order_title": "KAOS"}],
                     idnum=[{"qid": b["id"], "so_id": so_id, "order_number": "SO-2610-0007"}])
    it = out.items[0]
    assert (it.sales_order_id, it.sales_order_number, it.order_code, it.order_title, it.order_code_label) == \
        (str(so_id), "SO-2610-0007", "002-10-26", "KAOS", "Kode order")
    assert it.converted_to_type == "sales_order" and it.converted_to_id == str(so_id) and it.quote_number == "QUO-1"  # lama utuh
    assert out.total == 1


def test_daftar_tanpa_konversi_nol_tetap_membawa_label(monkeypatch):
    b = dict(_baris_daftar(), status="draft", converted_to_type=None, converted_to_id=None)
    _, out = _daftar(monkeypatch, baris=[b])
    it = out.items[0]
    assert it.sales_order_id is None and it.order_code is None and it.order_code_label == "Kode order"


def test_pencarian_menyertakan_so_hasil_di_count_dan_daftar_dengan_indeks_sama(monkeypatch):
    c, _ = _daftar(monkeypatch, search="002-10-26")
    norm = [" ".join(s.split()) for s in c.sql]
    count_sql = next(s for s in norm if "SELECT COUNT(*) FROM quotes" in s)
    list_sql = next(s for s in norm if "FROM quotes WHERE" in s and "ORDER BY created_at" in s)
    for sql in (count_sql, list_sql):
        assert "quotes.converted_to_type = 'sales_order'" in sql
        assert "so_c.order_number ILIKE $2" in sql and "so_c.order_code ILIKE $2" in sql and "so_c.order_title ILIKE $2" in sql
        assert "so_c.tenant_id = $1" in sql
    # parameter pola yang sama dipakai $2 di kueri hitung & daftar
    args_count = next(a for s, a in zip(c.sql, c.args) if "SELECT COUNT(*) FROM quotes" in s)
    assert args_count[0] == T and args_count[1] == "%002-10-26%"


def test_pencarian_beberapa_kata_tiap_kata_juga_ke_so(monkeypatch):
    c, _ = _daftar(monkeypatch, search="kaos 002-10-26")
    sql = next(" ".join(x.split()) for x in c.sql if "SELECT COUNT(*) FROM quotes" in " ".join(x.split()))
    assert sql.count("quotes.converted_to_type = 'sales_order'") == 2  # satu per kata
    assert "so_c.order_title ILIKE $2" in sql and "so_c.order_title ILIKE $3" in sql


def test_skema_aditif():
    assert BARU <= set(QuoteListItem.model_fields) and BARU <= set(QuoteDetail.model_fields)
    for m in (QuoteListItem, QuoteDetail):
        assert all(m.model_fields[f].default is None for f in BARU)
    # field lama tetap (kontrak FE live)
    assert {"converted_to_type", "converted_to_id", "quote_number", "is_expired"} <= set(QuoteListItem.model_fields)
    assert {"converted_to_type", "converted_to_id", "converted_at", "customer_phone", "business_name"} <= set(QuoteDetail.model_fields)


def test_detail_menempelkan_so_hasil():
    src = inspect.getsource(Q.get_quote_detail)
    assert "so_hasil_penawaran" in src and "**_so" in src
    assert "so_hasil_penawaran" in inspect.getsource(Q.list_quotes) and "**so_hasil[" in inspect.getsource(Q.list_quotes)
