"""Harness NYATA ubah penawaran terkirim (W-20261013-UBAH-QUOTE-TERKIRIM).
- kaos (boleh ditulis uji): jalur TULIS di transaksi yang di-ROLLBACK; sesudahnya dibuktikan tak ada jejak.
- grapgrap: pool READ ONLY (tulis apa pun -> galat) -- hanya penjaga/potret/tanggal QUO-2610-0018 yang dibaca.
Handler NYATA, satu objek modul (backend.api_gateway.app.*)."""
import asyncio
import json
import os
import sys
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend.api_gateway.app.routers import quotes as Q  # noqa: E402
from backend.api_gateway.app.schemas.quotes import UpdateQuoteRequest  # noqa: E402

GG, KAOS = "grapgrap-manado", "kaos-biru-konveksi"
gagal = 0


class Rollback(Exception):
    pass


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    if not ok:
        gagal += 1


def req(user_id, tenant):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": user_id, "tenant_id": tenant, "role": "ADMIN"}))


async def pemilik(c, t):
    return str(await c.fetchval(
        "SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id "
        "WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND r.is_active ORDER BY utr.is_primary DESC LIMIT 1", t))


async def jejak(c, qid):
    q = await c.fetchrow("SELECT status, notes, quote_date, updated_at, total_amount FROM quotes WHERE id = $1", qid)
    n = await c.fetchval("SELECT count(*) FROM audit_logs WHERE entity_id = $1::uuid AND entity_type = 'quotes'", str(qid))
    b = await c.fetch("SELECT id, quantity FROM quote_items WHERE quote_id = $1 ORDER BY sort_order, id", qid)
    return dict(q), n, [(str(x["id"]), x["quantity"]) for x in b]


async def main():
    tulis = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    baca = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2,
                                     server_settings={"default_transaction_read_only": "on"})

    # ---------------- grapgrap: BACA SAJA ----------------
    async with baca.acquire() as c:
        try:
            async with c.transaction():
                await c.execute("CREATE TEMP TABLE _x(i int)")
            cek(False, "kontrol: pool baca menolak tulis")
        except Exception as e:  # noqa: BLE001
            cek("read-only" in str(e), "kontrol: pool baca menolak tulis", type(e).__name__)
        g = await c.fetchrow("SELECT id, status, quote_date, expiry_date FROM quotes WHERE tenant_id = $1 AND quote_number = 'QUO-2610-0018'", GG)
        cek(g is not None and g["status"] in Q.STATUS_BOLEH_UBAH, "grapgrap QUO-2610-0018 berstatus yang kini boleh diubah",
            g and g["status"])
        try:
            await Q._tolak_bila_sudah_jadi_dokumen(c, GG, g["id"])
            cek(True, "grapgrap QUO-2610-0018 belum dirujuk SO/faktur (penjaga 409 lolos)")
        except HTTPException as e:
            cek(False, "grapgrap QUO-2610-0018 belum dirujuk SO/faktur", e.detail)
        pot = await Q._potret_penawaran(c, GG, g["id"])
        cek(pot["kepala"].get("id") == g["id"] and len(pot["baris"]) > 0, "potret grapgrap kepala+baris terbaca",
            f"{len(pot['baris'])} baris")
        try:
            Q._periksa_tanggal_terkirim(UpdateQuoteRequest(quote_date=g["quote_date"] - timedelta(days=1)), pot["kepala"],
                                        g["quote_date"])
            cek(True, "grapgrap: mundurkan tanggal penawaran 1 hari = lolos penjaga tanggal")
        except HTTPException as e:
            cek(False, "grapgrap: mundurkan tanggal", e.detail)
        pot_lain = await Q._potret_penawaran(c, KAOS, g["id"])
        cek(pot_lain["kepala"] == {}, "potret lintas tenant (kaos membaca id grapgrap) = kosong")

    # ---------------- kaos: TULIS di transaksi ROLLBACK ----------------
    async with tulis.acquire() as c:
        own = await pemilik(c, KAOS)
        ctx = {"tenant_id": KAOS, "user_id": __import__("uuid").UUID(own)}
        q = await c.fetchrow("""SELECT q.id, q.quote_number, q.status, q.quote_date, q.expiry_date FROM quotes q
                                WHERE q.tenant_id = $1 AND q.status IN ('sent','viewed')
                                  AND NOT EXISTS (SELECT 1 FROM sales_orders s WHERE s.tenant_id = q.tenant_id AND s.quote_id = q.id)
                                  AND EXISTS (SELECT 1 FROM quote_items i WHERE i.quote_id = q.id)
                                ORDER BY q.quote_date DESC LIMIT 1""", KAOS)
        cek(q is not None, "kaos punya penawaran terkirim tanpa rujukan", q and q["quote_number"])
        awal = await jejak(c, q["id"])

        # 1) catatan + tanggal pada penawaran terkirim
        try:
            async with c.transaction():
                badan = UpdateQuoteRequest(notes="TES E2E ubah sesudah dikirim", quote_date=q["quote_date"])
                r = await Q._ubah_penawaran(c, ctx, str(q["id"]), badan)
                st = await c.fetchval("SELECT status FROM quotes WHERE id = $1", q["id"])
                a = await c.fetchrow("""SELECT "eventType", metadata FROM audit_logs WHERE entity_id = $1::uuid
                                        AND entity_type = 'quotes' ORDER BY "createdAt" DESC LIMIT 1""", str(q["id"]))
                meta = json.loads(a["metadata"])
                cek(r.data["after_sent"] is True and st == q["status"], "kaos: diubah, status TETAP", st)
                cek(a["eventType"] == "QUOTE_UPDATED" and meta["ringkas"].startswith("Penawaran diubah sesudah dikirim"),
                    "kaos: riwayat 'diubah sesudah dikirim' tercatat di tx yang sama", meta["ringkas"])
                cek("notes" in meta["diff"] and meta["diff"]["notes"][1] == "TES E2E ubah sesudah dikirim",
                    "kaos: diff catatan [lama, baru]", json.dumps(meta["diff"])[:200])
                cek("quote_date" not in meta["diff"], "kaos: tanggal dikirim sama = tak masuk diff")
                raise Rollback
        except Rollback:
            pass

        # 2) baris: qty baris pertama +1 -> total berubah, baris 'changed'
        try:
            async with c.transaction():
                bs = await c.fetch("SELECT * FROM quote_items WHERE quote_id = $1 ORDER BY sort_order, id", q["id"])
                items = [{"item_id": str(b["item_id"]) if b["item_id"] else None, "description": b["description"],
                          "quantity": b["quantity"] + (1 if i == 0 else 0), "unit": b["unit"],
                          "unit_price": int(b["unit_price"]), "discount_percent": float(b["discount_percent"] or 0),
                          "tax_id": str(b["tax_id"]) if b["tax_id"] else None, "tax_rate": float(b["tax_rate"] or 0),
                          "group_name": b["group_name"]} for i, b in enumerate(bs)]
                r = await Q._ubah_penawaran(c, ctx, str(q["id"]), UpdateQuoteRequest(items=items))
                ch = r.data["lines"]["changed"]
                cek(len(ch) == 1 and ch[0]["line"] == 1 and "quantity" in ch[0]["changes"],
                    "kaos: baris 1 qty+1 tercatat 'changed'", json.dumps(ch)[:200])
                cek(r.data["total"][0] != r.data["total"][1] and "total_amount" in r.data["diff"],
                    "kaos: total lama != baru", str(r.data["total"]))
                raise Rollback
        except Rollback:
            pass

        cek(await jejak(c, q["id"]) == awal, "kaos: sesudah ROLLBACK nol jejak (kepala, audit, baris)")

    # 3) pratinjau lewat handler NYATA (pool tulis; handler sendiri ROLLBACK)
    async def gp():
        return tulis
    Q.get_pool = gp
    async with tulis.acquire() as c:
        awal = await jejak(c, q["id"])
    r = await Q.preview_edit_quote(req(own, KAOS), str(q["id"]), UpdateQuoteRequest(notes="TES E2E pratinjau"))
    cek(r.data.get("preview") is True and r.data["diff"].get("notes", [None, None])[1] == "TES E2E pratinjau",
        "pratinjau: diff catatan, preview=true")
    async with tulis.acquire() as c:
        cek(await jejak(c, q["id"]) == awal, "pratinjau: nol jejak di DB")
        for st in ("converted", "void", "declined", "accepted"):
            x = await c.fetchval("SELECT id FROM quotes WHERE tenant_id = $1 AND status = $2 LIMIT 1", KAOS, st)
            if not x:
                print(f"PASS (lewati) tak ada penawaran kaos berstatus {st}")
                continue
            try:
                await Q.preview_edit_quote(req(own, KAOS), str(x), UpdateQuoteRequest(notes="x"))
                cek(False, f"kaos {st}: terkunci")
            except HTTPException as e:
                cek(e.status_code == 400 and "Duplikat" in str(e.detail), f"kaos {st}: terkunci 400", e.detail)
    try:
        await Q.preview_edit_quote(req(own, KAOS), str(g["id"]), UpdateQuoteRequest(notes="x"))
        cek(False, "lintas tenant: id grapgrap dari kaos = 404")
    except HTTPException as e:
        cek(e.status_code == 404, "lintas tenant: id grapgrap dari kaos = 404", e.status_code)

    # 4) trg_quote_expiry: sunting penawaran TERKIRIM yang SUDAH LEWAT masa -> status tetap 'sent' (ROLLBACK)
    async with tulis.acquire() as c:
        hari = await Q.tanggal_dokumen(c, KAOS)
        x = await c.fetchrow("""SELECT q.id, q.quote_number, q.status FROM quotes q WHERE q.tenant_id = $1 AND q.status = 'sent'
                                AND q.expiry_date < $2 AND NOT EXISTS (SELECT 1 FROM sales_orders s
                                WHERE s.tenant_id = q.tenant_id AND s.quote_id = q.id) LIMIT 1""", KAOS, hari)
        cek(x is not None, "kaos punya penawaran sent yang sudah lewat masa", x and x["quote_number"])
        awal = await jejak(c, x["id"])
        try:
            async with c.transaction():
                await Q._ubah_penawaran(c, ctx, str(x["id"]), UpdateQuoteRequest(notes="TES E2E lewat masa"))
                st = await c.fetchval("SELECT status FROM quotes WHERE id = $1", x["id"])
                cek(st == "sent", "trigger expiry: sesudah diubah status TETAP sent (tak terdampar 'expired')", st)
                raise Rollback
        except Rollback:
            pass
        cek(await jejak(c, x["id"]) == awal, "trigger expiry: sesudah ROLLBACK nol jejak")

    # 5) PILL AKSI: Python == SQL di SEMUA penawaran; daftar ?aksi= == /summary (READ ONLY, kaos + grapgrap)
    from backend.api_gateway.app.services import penawaran_kedaluwarsa as PK

    async def gpb():
        return baca
    Q.get_pool = gpb
    async with baca.acquire() as c:
        for t in (GG, KAOS):
            hari = await Q.tanggal_dokumen(c, t)
            semua = await c.fetch("SELECT id, status, expiry_date, converted_to_id, total_amount FROM quotes WHERE tenant_id = $1", t)
            py = {k: {r["id"] for r in semua if PK.aksi_penawaran(r["status"], r["expiry_date"], hari, r["converted_to_id"]) == k}
                  for k in PK.AKSI}
            own_t = await pemilik(c, t)
            s = (await Q.get_quote_summary(req(own_t, t))).data
            for k in PK.AKSI:
                pred, par = PK.sql_aksi_berparam(k, 2, hari)
                sq = {r["id"] for r in await c.fetch(f"SELECT id FROM quotes WHERE tenant_id = $1 AND {pred}", t, *par)}
                lst = await Q.list_quotes(req(own_t, t), status="all", aksi=k, customer_id=None, start_date=None,
                                          end_date=None, search=None, skip=0, limit=100, page=1, offset=0)
                ids_l = {i.id for i in lst.items}
                nilai = sum(int(r["total_amount"]) for r in semua if r["id"] in py[k])
                ok = (py[k] == sq and {str(i) for i in sq} == ids_l and lst.total == len(sq)
                      and getattr(s, f"{k}_count") == len(sq) and getattr(s, f"{k}_value") == nilai
                      and all(i.aksi == k for i in lst.items))
                cek(ok, f"{t} aksi {k}: python=sql=daftar=ringkasan", f"{len(py[k])} | nilai {nilai}")
            # tanggal GESER di sekitar tiap tanggal berlaku nyata (sisa 4/3/0/-1 hari): segera_habis tak boleh kosong
            from datetime import timedelta as _td
            geser = sorted({r["expiry_date"] - _td(days=d) for r in semua if r["expiry_date"] for d in (4, 3, 0, -1)})
            beda, n_segera = [], 0
            for hx in geser:
                for k in PK.AKSI:
                    p_ = {r["id"] for r in semua
                          if PK.aksi_penawaran(r["status"], r["expiry_date"], hx, r["converted_to_id"]) == k}
                    pred, par = PK.sql_aksi_berparam(k, 2, hx)
                    s_ = {r["id"] for r in await c.fetch(f"SELECT id FROM quotes WHERE tenant_id = $1 AND {pred}", t, *par)}
                    n_segera += len(s_) if k == "segera_habis" else 0
                    if p_ != s_:
                        beda.append((hx.isoformat(), k, len(p_), len(s_)))
            cek(not beda and n_segera > 0, f"{t}: python==sql di {len(geser)} tanggal geser (segera_habis teruji {n_segera})",
                str(beda[:3]))
            terhitung = set().union(*py.values())
            sisa = {r["status"] for r in semua if r["id"] not in terhitung}
            cek(sisa <= {"converted", "declined", "void", "expired", "accepted"}, f"{t}: di luar aksi hanya status terjawab/batal",
                str(sorted(sisa)))

    await tulis.close()
    await baca.close()
    print(f"RINGKAS gagal={gagal}")


asyncio.run(main())
