"""Lapisan LLM cadangan Milkybook v2 (paket v2 03 §4, P5): dipanggil FE HANYA bila aturan menghasilkan unknown/clarify.

- Keluaran tertutup: model wajib menutup lewat tool `jawab` (strict) -> divalidasi pydantic. Tak sah / tanpa jawab =
  clarify (tanya balik), tidak menebak.
- Tool BACA saja: search_customers, search_items (inti yang sama dengan rute /v2/pelanggan, /v2/barang), open_invoices
  (compute_customer_ar, Law 16), stock (inventory_ledger), sales_summary (omzet per periode dari JURNAL, Law 16; tiket CH05
  2 Nov). Tidak ada jalur tulis (dikunci tes pemindai).
- Penjaga angka (Law 0 / 3.1): setiap angka uang/persen di `reply` harus ada di hasil tool giliran ini atau ketikan
  pengguna; selain itu jawaban diganti tanya balik (sumber 'penjaga').
- Pagar biaya SAMA dengan v1: biaya bulan ini (asisten_aksi 'giliran') >= batas tenant -> tanpa panggilan model.
- Log ketikan (V470, putusan pemilik 1 Nov: teks 30 hari): satu baris per panggilan, buang > 30 hari saat menulis.
  Isi ketikan TIDAK pernah ke logger."""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import cari_barang, cari_pelanggan
from .draf import TAHAP

logger = logging.getLogger(__name__)

MAKS_PUTARAN = 4
UMUR_LOG_HARI = 30
EFFORT_BAWAAN = "low"  # tugas tafsir pendek (klasifikasi + isi slot); skill claude-api: chat/klasifikasi -> low
PESAN_TANYA_BALIK = "Maaf, saya belum menangkap maksudnya. Bisa diulang dengan kata lain?"
PESAN_SIBUK = "Asisten sedang sibuk. Coba ulangi sebentar lagi, atau lanjutkan dengan pilihan di layar."
PESAN_BATAS = "Batas pemakaian Milkybook bulan ini untuk usaha Anda sudah tercapai. Lanjutkan dengan pilihan di layar."

INTENT = Literal["answer_question", "check_exists", "fill_slot", "switch_task", "clarify"]
SLOT = Literal["customer", "items", "valid", "dp", "tax", "none"]


class Giliran(BaseModel):
    peran: Literal["pengguna", "bot"]
    teks: str = Field(..., max_length=500)


class MintaTafsir(BaseModel):
    teks: str = Field(..., min_length=1, max_length=500)
    tahap: Optional[TAHAP] = None
    ringkasan: dict = Field(default_factory=dict)  # ringkasan draf TANPA harga master lengkap (03 §4)
    riwayat: list[Giliran] = Field(default_factory=list, max_length=6)

    @field_validator("ringkasan")
    @classmethod
    def _ringkasan_kecil(cls, v):
        if len(json.dumps(v, ensure_ascii=False, default=str)) > 4000:
            raise ValueError("ringkasan terlalu besar")
        return v


class Jawab(BaseModel):
    intent: INTENT
    slot: SLOT = "none"
    value: str = Field("", max_length=200)
    reply: str = Field(..., min_length=1, max_length=400)


DEFINISI_OMZET = "pendapatan bersih (sesudah retur/diskon), tanpa PPN, dari jurnal terposting"
PERIODE = ("hari_ini", "kemarin", "minggu_ini", "bulan_ini", "bulan_lalu", "tahun_ini")
_BULAN = ("", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November",
          "Desember")


def _tgl(d: date) -> str:
    return f"{d.day} {_BULAN[d.month]} {d.year}"


def periode(nama: str, hari_ini: date) -> Optional[tuple[date, date, str]]:
    """Periode tertutup -> (mulai, sampai, label). Periode berjalan berakhir HARI INI (tanggal bisnis tenant), bukan
    akhir bulan. Nama di luar PERIODE -> None."""
    if nama == "hari_ini":
        return hari_ini, hari_ini, f"hari ini ({_tgl(hari_ini)})"
    if nama == "kemarin":
        k = hari_ini - timedelta(days=1)
        return k, k, f"kemarin ({_tgl(k)})"
    if nama == "minggu_ini":
        m = hari_ini - timedelta(days=hari_ini.weekday())
        return m, hari_ini, f"minggu ini ({_tgl(m)} s/d {_tgl(hari_ini)})"
    if nama == "bulan_ini":
        m = hari_ini.replace(day=1)
        return m, hari_ini, f"bulan ini ({_tgl(m)} s/d {_tgl(hari_ini)})"
    if nama == "bulan_lalu":
        akhir = hari_ini.replace(day=1) - timedelta(days=1)
        return akhir.replace(day=1), akhir, f"bulan lalu ({_BULAN[akhir.month]} {akhir.year})"
    if nama == "tahun_ini":
        m = hari_ini.replace(month=1, day=1)
        return m, hari_ini, f"tahun ini ({_tgl(m)} s/d {_tgl(hari_ini)})"
    return None


def _skema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TOOLS = [
    {"name": "search_customers", "description": "Cari pelanggan usaha ini menurut nama (boleh sebagian, toleran salah ketik). "
     "Hasil: id, nama, telepon, fakta pesanan.", "strict": True,
     "input_schema": _skema({"q": {"type": "string"}})},
    {"name": "search_items", "description": "Cari barang/jasa usaha ini menurut nama atau kode. Hasil: id, nama, harga master, "
     "satuan, ukuran.", "strict": True, "input_schema": _skema({"q": {"type": "string"}})},
    {"name": "open_invoices", "description": "Tagihan (faktur) yang belum lunas untuk satu pelanggan. customer_id dari "
     "search_customers.", "strict": True, "input_schema": _skema({"customer_id": {"type": "string"}})},
    {"name": "stock", "description": "Stok satu barang. item_id dari search_items.", "strict": True,
     "input_schema": _skema({"item_id": {"type": "string"}})},
    {"name": "sales_summary", "description": "Omzet usaha ini dalam satu periode: pendapatan usaha bersih dari jurnal "
     "(penjualan dikurangi retur/diskon; tanpa pendapatan lain). Hasil: label periode, tanggal mulai-sampai, omzet.",
     "strict": True, "input_schema": _skema({"periode": {"type": "string", "enum": list(PERIODE)}})},
    {"name": "jawab", "description": "WAJIB dipanggil sekali untuk menutup giliran dengan jawaban akhir.", "strict": True,
     "input_schema": _skema({
         "intent": {"type": "string", "enum": list(INTENT.__args__)},
         "slot": {"type": "string", "enum": list(SLOT.__args__)},
         "value": {"type": "string"},
         "reply": {"type": "string"},
     })},
]
TOOL_BACA = ("search_customers", "search_items", "open_invoices", "stock", "sales_summary")

SISTEM = """Anda penafsir ketikan untuk asisten penawaran harga Milkybook (aplikasi pembukuan usaha kecil Indonesia).
Aturan program sudah gagal memahami ketikan ini; tugas Anda menentukan maksudnya, lalu menutup dengan tool `jawab`.

Maksud (intent):
- fill_slot: pengguna mengisi bagian draf (slot customer/items/valid/dp/tax); `value` = isi yang dimaksud, apa adanya.
- check_exists: pengguna bertanya apakah pelanggan/barang sudah ada -> cari dulu dengan tool, lalu jawab.
- answer_question: pertanyaan di tengah jalan (tagihan terbuka, stok, harga, omzet/penjualan per periode) -> cari dengan
  tool, jawab singkat. Omzet: sales_summary dengan periode yang paling cocok; jawaban WAJIB menyebut label periode dan
  `definisi` dari hasil alat secara ringkas. Omzet 0 dijawab Rp0 apa adanya. Omzet per pelanggan/per barang/per
  periode di luar pilihan alat BELUM bisa: katakan jujur, jangan mengarang angka.
- switch_task: pengguna ingin pindah ke pekerjaan lain (mis. catat biaya, batal, mulai baru); `value` = pekerjaan itu.
- clarify: maksud tidak jelas -> tanya balik dengan pilihan, JANGAN menebak.

Aturan keras:
- Anda tidak pernah mengubah data. Perubahan selalu lewat kartu tinjau di layar, bukan oleh Anda.
- Setiap angka (rupiah, persen, jumlah tagihan, stok) di `reply` HARUS berasal dari hasil tool pada giliran ini.
  Jangan menghitung sendiri (jumlah, selisih, total). Bila angka tidak ada di hasil tool, jangan sebut angkanya.
- fill_slot: angka atau ungkapan jumlah ditaruh di `value` (mis. "separo" -> value "50%"); `reply` cukup konfirmasi
  singkat TANPA angka, karena kartu tinjau di layar yang menampilkan angkanya.
- Rupiah ditulis format Indonesia: Rp185.000 (titik pemisah ribuan), bukan 185000.
- Bila beberapa tool tidak saling bergantung, panggil sekaligus dalam satu giliran.
- `reply` satu atau dua kalimat bahasa Indonesia santai dan sopan. Tanpa markdown.
- Bila slot tidak relevan, isi slot "none" dan value "".
- Selalu tutup dengan memanggil `jawab` tepat sekali."""

# ---------------------------------------------------------------- tool BACA (SQL konstanta, nol INSERT/UPDATE/DELETE)
SQL_TAGIHAN = """
SELECT invoice_number, invoice_date, due_date, outstanding AS sisa
FROM compute_customer_ar($1, $2)
WHERE outstanding > 0 AND invoice_id IS NOT NULL
ORDER BY due_date ASC, invoice_date ASC
LIMIT 10
"""
# Omzet = pendapatan usaha bersih per periode dari JURNAL (Law 16; pola _FLOW_CREDIT_NORMAL_SQL driver_deltas, akun
# REVENUE saja -- retur/diskon penjualan ikut sebagai akun REVENUE berdebit; OTHER_INCOME tidak termasuk omzet).
SQL_OMZET = """
SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) AS omzet
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.tenant_id = je.tenant_id
WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.journal_date BETWEEN $2 AND $3 AND coa.account_type = 'REVENUE'
"""
SQL_PELANGGAN_ADA = "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL"
SQL_STOK = """
SELECT p.nama_produk AS nama, p.item_type AS jenis, p.base_unit AS satuan,
  COALESCE((SELECT SUM(il.quantity_in) - SUM(il.quantity_out) FROM inventory_ledger il
            WHERE il.product_id = p.id AND il.tenant_id = p.tenant_id), 0) AS stok
FROM products p WHERE p.id = $1 AND p.tenant_id = $2 AND p.deleted_at IS NULL
"""


def _uuid(x) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(x))
    except (ValueError, TypeError):
        return None


def _rp(v):
    return cari_barang.rupiah(v)


async def jalankan_tool(conn, tenant_id: str, nama: str, args: dict) -> dict:
    """Satu tool BACA -> hasil JSON kecil. Galat masukan = {"galat": ...} (bukan exception ke model)."""
    if nama == "search_customers":
        return {"items": await cari_pelanggan.cari(conn, tenant_id, str(args.get("q", ""))[:80], 6)}
    if nama == "search_items":
        return {"items": await cari_barang.cari(conn, tenant_id, str(args.get("q", ""))[:80], 6)}
    if nama == "open_invoices":
        cid = _uuid(args.get("customer_id"))
        if not cid or not await conn.fetchrow(SQL_PELANGGAN_ADA, cid, tenant_id):
            return {"galat": "pelanggan tidak ditemukan"}
        rows = await conn.fetch(SQL_TAGIHAN, tenant_id, str(cid))
        tagihan = [{"nomor": r["invoice_number"], "jatuh_tempo": r["due_date"].isoformat() if r["due_date"] else None,
                    "sisa": _rp(r["sisa"])} for r in rows]
        return {"tagihan": tagihan, "jumlah_tagihan": len(tagihan),
                "total_sisa": _rp(sum((Decimal(str(r["sisa"])) for r in rows), Decimal(0)))}
    if nama == "stock":
        iid = _uuid(args.get("item_id"))
        r = await conn.fetchrow(SQL_STOK, iid, tenant_id) if iid else None
        if not r:
            return {"galat": "barang tidak ditemukan"}
        if r["jenis"] != "goods":
            return {"nama": r["nama"], "stok": None, "keterangan": "barang ini tidak dilacak stoknya"}
        return {"nama": r["nama"], "stok": _rp(r["stok"]), "satuan": r["satuan"]}
    if nama == "sales_summary":
        p = periode(str(args.get("periode", "")), await hari_ini_tenant(conn, tenant_id))
        if not p:
            return {"galat": "periode tidak dikenal"}
        mulai, sampai, label = p
        return {"periode": label, "mulai": mulai.isoformat(), "sampai": sampai.isoformat(),
                "omzet": _rp(await conn.fetchval(SQL_OMZET, tenant_id, mulai, sampai)), "definisi": DEFINISI_OMZET}
    return {"galat": f"tool tidak dikenal: {nama}"}


async def hari_ini_tenant(conn, tenant_id: str) -> date:
    """Tanggal bisnis tenant (zona tenant), sama dengan tanggal dokumen."""
    from ...utils.tanggal_tenant import tanggal_dokumen
    return await tanggal_dokumen(conn, tenant_id)


# ---------------------------------------------------------------- log ketikan (V470)
SQL_BUANG_LOG = f"DELETE FROM milkybook_v2_tafsir_log WHERE tenant_id = $1 AND dibuat < NOW() - INTERVAL '{UMUR_LOG_HARI} days'"
SQL_LOG = ("INSERT INTO milkybook_v2_tafsir_log (tenant_id, user_id, tahap, ketikan, intent, sumber, latensi_ms, model, "
           "biaya_usd) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)")


async def tulis_log(conn, tenant_id, user_id, body: MintaTafsir, hasil: dict, latensi_ms: int, model, biaya) -> None:
    """Log = bahan aturan, bukan pagar: gagal tulis dicatat (jenis galat saja, TANPA isi ketikan), jawaban tetap keluar."""
    try:
        async with conn.transaction():
            await conn.execute(SQL_BUANG_LOG, tenant_id)
            await conn.execute(SQL_LOG, tenant_id, user_id, body.tahap, body.teks, hasil["intent"], hasil["sumber"],
                               max(0, int(latensi_ms)), model, biaya)
    except Exception as e:
        logger.error("[MILKYBOOK v2] log tafsir gagal: %s", type(e).__name__)


# ---------------------------------------------------------------- inti
def _pesan_awal(body: MintaTafsir) -> str:
    konteks = {"tahap": body.tahap, "ringkasan_draf": body.ringkasan,
               "riwayat": [g.model_dump() for g in body.riwayat]}
    return ("Konteks (JSON):\n" + json.dumps(konteks, ensure_ascii=False, default=str)
            + "\n\nKetikan pengguna:\n" + body.teks)


def _tanya_balik(sumber: str, reply: str = PESAN_TANYA_BALIK, tool_calls=None) -> dict:
    return {"intent": "clarify", "slot": "none", "value": "", "reply": reply, "tool_calls": tool_calls or [],
            "sumber": sumber}


async def tafsir(conn, user: dict, body: MintaTafsir, *, pemanggil, audit, batas_fn: Callable,
                 effort: str = EFFORT_BAWAAN, jam: Callable = time.monotonic) -> dict:
    """-> {intent, slot, value, reply, tool_calls[{name,args}], sumber}. Tidak pernah melempar galat model ke FE."""
    from ..inti.audit import BarisAudit
    from ..inti.model import GalatModel, biaya_usd
    from ..inti.penjaga_angka import periksa

    tenant_id, user_id = str(user["tenant_id"]), str(user["user_id"])
    mulai = jam()
    model_nama, biaya, usage_total = getattr(pemanggil, "model", None), 0.0, {}

    batas = batas_fn(tenant_id)
    terpakai = await audit.biaya_bulan_ini(tenant_id) if batas is not None else None
    if terpakai is not None and terpakai >= batas:  # galat baca (None) = fail-open seperti v1; batas keras = Console
        hasil = _tanya_balik("batas", PESAN_BATAS)
        await tulis_log(conn, tenant_id, user_id, body, hasil, int((jam() - mulai) * 1000), None, None)
        return hasil

    if pemanggil is None:  # tanpa MILKYBOOK_ANTHROPIC_API_KEY: TIDAK jatuh ke ANTHROPIC_API_KEY lain (aturan pemilik)
        hasil = _tanya_balik("galat", PESAN_SIBUK)
        await tulis_log(conn, tenant_id, user_id, body, hasil, int((jam() - mulai) * 1000), None, None)
        return hasil

    messages = [{"role": "user", "content": _pesan_awal(body)}]
    sumber_angka: list = [body.teks]
    dipanggil: list = []
    hasil = None
    try:
        for _ in range(MAKS_PUTARAN):
            r = await pemanggil(system=[{"type": "text", "text": SISTEM}], messages=messages, tools=TOOLS, effort=effort)
            model_nama = r.model or model_nama
            for k, v in (r.usage or {}).items():
                if isinstance(v, (int, float)):
                    usage_total[k] = usage_total.get(k, 0) + v
            biaya += biaya_usd(r.model or model_nama, r.usage or {})
            messages.append({"role": "assistant", "content": r.konten})  # append-only (preserved thinking)
            calls = r.tool_calls
            akhir = next((c for c in calls if c.nama == "jawab"), None)
            if akhir is not None:
                try:
                    j = Jawab.model_validate(akhir.masukan)
                except ValidationError:
                    hasil = _tanya_balik("tak_sah", tool_calls=dipanggil)
                else:
                    hasil = {**j.model_dump(), "tool_calls": dipanggil, "sumber": "llm"}
                    if periksa(j.reply, sumber_angka):
                        hasil = _tanya_balik("penjaga", tool_calls=dipanggil)
                break
            baca = [c for c in calls if c.nama in TOOL_BACA]
            if not baca:  # teks bebas tanpa jawab / tool asing / stop lain -> tak sah
                hasil = _tanya_balik("tak_sah", tool_calls=dipanggil)
                break
            hasil_tool = []
            for c in calls:
                isi = await jalankan_tool(conn, tenant_id, c.nama, c.masukan) if c.nama in TOOL_BACA \
                    else {"galat": "tool tidak tersedia"}
                dipanggil.append({"name": c.nama, "args": c.masukan})
                sumber_angka.append(isi)
                hasil_tool.append({"type": "tool_result", "tool_use_id": c.id,
                                   "content": json.dumps(isi, ensure_ascii=False, default=str)})
            messages.append({"role": "user", "content": hasil_tool})  # SEMUA hasil dalam satu pesan
        else:
            hasil = _tanya_balik("tak_sah", tool_calls=dipanggil)
    except GalatModel as e:
        logger.warning("[MILKYBOOK v2] tafsir galat model: %s", e.jenis)  # tanpa isi ketikan
        hasil = _tanya_balik("galat", PESAN_SIBUK, tool_calls=dipanggil)

    latensi = int((jam() - mulai) * 1000)
    if usage_total:
        await audit.tulis(BarisAudit(
            tenant_id=tenant_id, user_id=user_id, sesi_id="v2-tafsir", peristiwa="giliran", kelas="v2_tafsir",
            model=model_nama, hasil=f"{hasil['intent']}/{hasil['sumber']}",
            token_masuk=int(usage_total.get("input_tokens", 0)), token_keluar=int(usage_total.get("output_tokens", 0)),
            token_cache_baca=int(usage_total.get("cache_read_input_tokens", 0)),
            token_cache_tulis=int(usage_total.get("cache_creation_input_tokens", 0)), biaya_usd=round(biaya, 6)))
    await tulis_log(conn, tenant_id, user_id, body, hasil, latensi, model_nama,
                    Decimal(str(round(biaya, 6))) if usage_total else None)
    return hasil
