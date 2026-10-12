"""13 Okt 2026 (W-20261013-ITEMFIELD): POST/PUT /api/items menyimpan SEMUA field yang dikirim form.
(1) sales_tax_id/purchase_tax_id diterima, divalidasi milik tenant + aktif + arah, nama teks dari tax_codes;
(2) bisa_dikirim satu INSERT (lihat test_kirim_non_stok); (3) field belum didukung yang berisi -> 422, kunci tak dikenal
-> WARNING (bukan dibuang diam tanpa jejak); chat `description` -> deskripsi; (4) SQL _item_jebakan_ubah_tipe berkutip."""
import asyncio
import inspect
import logging
import re
import uuid

import pytest
from fastapi import HTTPException

from app.routers import items as I
from app.schemas.items import CreateItemRequest as C, UpdateItemRequest as U

T = "tenant-uji"
B = {"name": "Kaos", "base_unit": "pcs"}


def _galat(fn, *a):
    with pytest.raises(HTTPException) as e:
        r = fn(*a)
        if inspect.iscoroutine(r):
            asyncio.run(r)
    return e.value


# ---- skema ----
def test_skema_menerima_id_pajak_post_dan_put():
    sid, pid = uuid.uuid4(), uuid.uuid4()
    c = C(**B, sales_tax_id=str(sid), purchase_tax_id=str(pid))
    assert c.sales_tax_id == sid and c.purchase_tax_id == pid and c.kunci_asing == []
    u = U(sales_tax_id=str(sid))
    assert u.model_dump(exclude_unset=True) == {"sales_tax_id": sid}


def test_chat_description_jadi_deskripsi():
    assert C(**B, description="dari chat").deskripsi == "dari chat"
    assert C(**B, deskripsi="form").deskripsi == "form"
    assert U(description="x").model_dump(exclude_unset=True) == {"deskripsi": "x"}


def test_kunci_asing_dicatat_bukan_ditolak():
    c = C(**B, warna="merah", _batch_id="b1")
    assert c.kunci_asing == ["_batch_id", "warna"]
    assert "warna" not in c.model_dump()
    assert U(name="x", ukuran=1).kunci_asing == ["ukuran"]


@pytest.mark.parametrize("k,v", [("price_levels", [{"name": "Grosir", "price": 1}]), ("quantity_pricing", [{"min_qty": 1}]),
                                  ("quantity_pricing_enabled", True), ("default_bin_id", str(uuid.uuid4())),
                                  ("is_matrix_parent", True)])
def test_field_belum_didukung_berisi_422(k, v):
    for body in (C(**B, **{k: v}), U(**{k: v})):
        e = _galat(I.periksa_badan_item, body, "uji")
        assert e.status_code == 422 and e.detail["code"] == "FIELD_BELUM_DIDUKUNG" and e.detail["field"] == k


def test_kontrol_field_belum_didukung_kosong_lolos():
    """Form klasik SELALU mengirim kelima kunci kosong -> tak boleh pecah."""
    kosong = dict(price_levels=None, quantity_pricing=None, quantity_pricing_enabled=False, default_bin_id=None,
                  is_matrix_parent=False)
    I.periksa_badan_item(C(**B, **kosong), "uji")
    I.periksa_badan_item(U(**{**kosong, "price_levels": []}), "uji")


def test_kunci_asing_menulis_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=I.logger.name):
        I.periksa_badan_item(C(**B, warna="merah"), "POST /items")
    assert "[ITEM_KUNCI_ASING]" in caplog.text and "warna" in caplog.text


# ---- validasi pajak ----
class ConnPajak:
    def __init__(self, baris):
        self.baris, self.q = baris, []

    async def fetchrow(self, sql, *a):
        self.q.append((" ".join(sql.split()), a))
        return self.baris


@pytest.mark.parametrize("baris,arah,salah", [
    (None, "output", "tidak ditemukan"),
    ({"name": "PPN 11% Keluaran", "direction": "output", "is_active": False}, "output", "tidak aktif"),
    ({"name": "PPN 11% Masukan", "direction": "input", "is_active": True}, "output", "bukan pajak penjualan"),
    ({"name": "PPN 11% Keluaran", "direction": "output", "is_active": True}, "input", "bukan pajak pembelian"),
])
def test_pajak_tak_sah_422(baris, arah, salah):
    e = _galat(I.nama_pajak_item, ConnPajak(baris), T, uuid.uuid4(), "sales_tax_id", arah)
    assert e.status_code == 422 and e.detail["code"] == "PAJAK_ITEM_TIDAK_SAH" and salah in e.detail["message"]


@pytest.mark.parametrize("arah_kode,arah", [("output", "output"), ("input", "input"), (None, "output"), ("", "input")])
def test_pajak_sah_mengembalikan_nama_dan_berpagar_tenant(arah_kode, arah):
    tid = uuid.uuid4()
    c = ConnPajak({"name": "PPh 23 - Jasa", "direction": arah_kode, "is_active": True})
    assert asyncio.run(I.nama_pajak_item(c, T, tid, "f", arah)) == "PPh 23 - Jasa"
    sql, args = c.q[0]
    assert "tenant_id = $2" in sql and args == (tid, T)


def test_pajak_kosong_tanpa_kueri():
    c = ConnPajak(None)
    assert asyncio.run(I.nama_pajak_item(c, T, None, "f", "output")) is None and c.q == []


# ---- jalur handler (sumber) ----
def test_insert_create_memuat_id_pajak_dan_bisa_dikirim_dengan_placeholder_cocok():
    src = inspect.getsource(I.create_item)
    q = src[src.index('INSERT INTO products ('):src.index('RETURNING id')]
    kolom = [k.strip() for k in q[q.index("(") + 1:q.index(")")].split(",")]
    nilai = re.findall(r"\$(\d+)", q)
    assert {"sales_tax_id", "purchase_tax_id", "bisa_dikirim"} <= set(kolom)
    assert len(kolom) - 2 == len(nilai) == max(map(int, nilai)) == 39  # created_at/updated_at = NOW()
    assert 'nama_pajak_item(conn, tenant_id, body.sales_tax_id, "sales_tax_id", "output")' in src
    assert 'nama_pajak_item(conn, tenant_id, body.purchase_tax_id, "purchase_tax_id", "input")' in src
    assert 'periksa_badan_item(body, "POST /items")' in src


def test_update_memetakan_id_pajak():
    src = inspect.getsource(I.update_item)
    assert '"sales_tax_id": "sales_tax_id"' in src and '"purchase_tax_id": "purchase_tax_id"' in src
    assert 'body_dict["sales_tax"] = await nama_pajak_item(' in src
    assert 'periksa_badan_item(body, "PUT /items/{id}")' in src


def test_sql_jebakan_ubah_tipe_berkutip():
    """ebdaf643 menulis string_agg(..., , ) dan IN (posted, ...) tanpa kutip -> galat sintaks (500) di PUT ubah tipe."""
    src = inspect.getsource(I._item_jebakan_ubah_tipe)
    assert "string_agg(DISTINCT si.invoice_number, ', ')" in src
    assert "si.status IN ('posted', 'partial', 'paid')" in src
    assert ", , )" not in src
