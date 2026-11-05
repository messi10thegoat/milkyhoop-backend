"""ppv-revaluasi-wac: tes PERILAKU sambungan post_bill (permintaan MASTER): rencana_grn + hitung_debit + _emit + revaluasi_ppv ASLI;
hanya DB & kolaborator lain dipalsukan. Pasangan kartu stok tertulis, bertaut jurnal posting, dan Dr Persediaan == Δ kartu stok."""
import os
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import bills_service as BS  # noqa: E402
from app.services import tagihan_aksi as TA  # noqa: E402
from app.services import revaluasi_ppv as RV  # noqa: E402

BILL, BI, PID, GI = uuid4(), uuid4(), uuid4(), str(uuid4())


class _K:
    def __init__(self, ledger=None, unit="pcs", total="9324000", qty="240"):
        self.ledger = ledger or [{"product_id": PID, "quantity_in": D(240), "quantity_out": D(0), "unit_cost": D(35000), "warehouse_id": "G"}]
        self.exec, self.unit, self.total, self.qty = [], unit, D(total), D(qty)

    def is_in_transaction(self):
        return True

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()

    def _agr(self):
        q = sum((r["quantity_in"] - r["quantity_out"] for r in self.ledger), D(0))
        return q, sum((r["quantity_in"] * r["unit_cost"] - r["quantity_out"] * r["unit_cost"] for r in self.ledger), D(0))

    async def fetchrow(self, sql, *a):
        if "FROM bills" in sql:
            return {"id": BILL, "status_v2": "draft", "invoice_number": "BILL-T", "vendor_name": "V", "vendor_id": None,
                    "issue_date": date(2026, 11, 6), "due_date": date(2026, 12, 6), "grand_total": self.total, "tax_amount": D(0),
                    "tax_code_id": None, "tax_rate": 0, "dpp": None}
        if "FROM goods_receipt_items" in sql:
            return {"warehouse_id": "G", "item_code": "BMO2", "nama_produk": "Bimoli"}
        if "FROM inventory_ledger" in sql:
            q, n = self._agr()
            return {"q": q, "nilai": n}
        if "FROM warehouses" in sql:
            return {"id": "G"}
        return None

    async def fetchval(self, sql, *a):
        if "get_next_journal_number" in sql:
            return "PJ-T"
        if "get_weighted_average_cost" in sql:
            return D(35000)
        if "FROM inventory_ledger" in sql:
            return self._agr()[0]
        return None

    async def fetch(self, sql, *a):
        if "bill_items" in sql:
            return [{"id": BI, "product_id": PID, "quantity": self.qty, "unit": self.unit, "subtotal": self.total, "discount_amount": D(0),
                     "net": self.total, "pajak": D(0), "purchase_order_item_id": uuid4(), "bonus": D(0), "po_unit": self.unit, "po_id": None,
                     "item_type": "goods", "track_inventory": True, "purchase_account_id": None, "total": self.total, "tax_amount": D(0)}]
        return []

    async def execute(self, sql, *a):
        self.exec.append((sql, a))
        if "INSERT INTO inventory_ledger" in sql:
            self.ledger.append({"product_id": a[2], "quantity_in": a[10], "quantity_out": a[11], "unit_cost": a[13], "warehouse_id": a[16],
                                "journal_id": a[17], "source_type": a[7]})


def _pasang(monkeypatch, butir_isi):
    async def nol(*a, **k):
        return None

    async def kosong(*a, **k):
        return []

    async def butir(conn, tid, items):  # bawaan: 240 pcs diterima @35.000, ditagih 9.324.000 -> selisih 924.000
        isi = butir_isi or {"qty": D(240), "grni": D("8400000"), "tagih": D("9324000")}
        return [{"bill_item_id": BI, "po_item_id": items[0]["purchase_order_item_id"], "qty_tagih": isi["qty"], "qty_grn": isi["qty"],
                 "nilai_grni": isi["grni"], "nilai_tagih_grn": isi["tagih"], "selisih": isi["tagih"] - isi["grni"], "qty_inbound": D(0),
                 "perlu_inbound": False, "tautan": [{"gr_item_id": GI, "qty": isi["qty"], "nilai": isi["grni"]}]}]

    async def blok_posting(conn, tid, bill):
        return [], {"bulat": {"total": bill["grand_total"], "selisih": D(0)}, "vat": None, "ap": "AP", "debit": "INV-DEF"}

    async def peran(conn, tid, role):
        return {"GOODS_RECEIVED_NOT_INVOICED": "GRNI", "PURCHASE_PRICE_VARIANCE": "PPV"}.get(str(getattr(role, "value", role)), str(role))

    async def akun_stok(conn, tid, pid):
        return {"inventory_account_id": "PERS"}

    async def aktif(conn, tid):
        return True

    from app.services import grn_tagih as GT, role_resolver as RR, inventory_helpers as IH, po_daftar as PD, audit_dokumen as AD
    from app.services import ump_terap_otomatis as UTO
    monkeypatch.setattr(BS, "_ensure_bills_service_role_preconditions", nol)
    monkeypatch.setattr(TA, "blok_posting", blok_posting)
    monkeypatch.setattr(TA, "_blok_qty_po", kosong)
    monkeypatch.setattr(GT, "rencana_posting_baris", butir)
    monkeypatch.setattr(GT, "tulis_tautan", nol)
    monkeypatch.setattr(RR, "resolve_account_id_by_role", peran)
    monkeypatch.setattr(IH, "resolve_inventory_accounts", akun_stok)
    monkeypatch.setattr(RV, "aktif", aktif)
    monkeypatch.setattr(PD, "sinkron_po_bill", nol)
    monkeypatch.setattr(AD, "catat_dokumen", nol)
    monkeypatch.setattr(UTO, "terapkan_otomatis", kosong)



class _Pool:
    def __init__(self, k):
        self.k = k

    @asynccontextmanager
    async def acquire(self):
        yield self.k


@pytest.mark.asyncio
async def test_post_bill_menulis_pasangan_bertaut_jurnal_dan_persediaan_sama_dengan_delta(monkeypatch):
    _pasang(monkeypatch, None)
    k = _K()

    class _PoolLama:
        @asynccontextmanager
        async def acquire(self):
            yield k

    awal = k._agr()[1]
    r = await BS.BillsService(_PoolLama()).post_bill("t", BILL, None)
    assert r["success"], r
    je = [a for s, a in k.exec if "INSERT INTO journal_entries" in s][0]
    jid = je[0]
    jl = {a[3]: (a[4] if "VALUES ($1, $2, $3, $4, $5, 0, $6)" in s else -a[4]) for s, a in k.exec if "INSERT INTO journal_lines" in s}
    pasangan = [x for x in k.ledger if x.get("source_type") == RV.SUMBER]
    assert len(pasangan) == 2 and all(x["journal_id"] == jid for x in pasangan)       # bertaut jurnal posting INI
    delta = k._agr()[1] - awal
    assert delta == D("924000") and jl.get("PERS") == delta                          # Dr Persediaan == Δ kartu stok
    assert jl.get("GRNI") == D("8400000") and "PPV" not in jl                         # stok utuh: nol ke 5-10400


@pytest.mark.asyncio
async def test_post_bill_karton_pcs_sebagian_keluar_basis_satuan_dasar(monkeypatch):
    """ROW 99: PO 20 karton (x6 = 120 pcs) @6.000/karton, 60 pcs sudah keluar, ditagih 132.000 (selisih 12.000) -> porsi stok 60/120 = 50%:
    Persediaan 6.000, PPV 6.000. Dulu basis = 20 (karton) -> min(1, 60/20) = 100% -> Persediaan 12.000 (terlalu besar)."""
    _pasang(monkeypatch, {"qty": D(20), "grni": D("120000"), "tagih": D("132000")})
    from app.services import satuan_dasar as SD

    async def konversi(conn, tid, pid, unit, qty, mode, aksi="Pengiriman"):
        assert unit == "karton"
        return {"qty_dasar": D(str(qty)) * 6, "faktor": D(6), "satuan": unit, "satuan_dasar": "pcs", "blok": None}

    monkeypatch.setattr(SD, "qty_dasar", konversi)
    k = _K(ledger=[{"product_id": PID, "quantity_in": D(120), "quantity_out": D(0), "unit_cost": D(1000), "warehouse_id": "G"},
                   {"product_id": PID, "quantity_in": D(0), "quantity_out": D(60), "unit_cost": D(1000), "warehouse_id": "G"}],
           unit="karton", total="132000", qty="20")
    awal = k._agr()[1]
    r = await BS.BillsService(_Pool(k)).post_bill("t", BILL, None)
    assert r["success"], r
    jl = {a[3]: (a[4] if "VALUES ($1, $2, $3, $4, $5, 0, $6)" in s else -a[4]) for s, a in k.exec if "INSERT INTO journal_lines" in s}
    assert k._agr()[1] - awal == D("6000") == jl.get("PERS") and jl.get("PPV") == D("6000")
