"""Kerangka skenario journey (dipakai jalan_skenario.py). Satu objek J per lengan:
  A = router NYATA di app mini + middleware penyetel request.state.user (TANPA authz);
  B = backend.api_gateway.app.main PENUH + lifespan NYATA (middleware izin nyata); yang distub HANYA batas luar:
      validate_token gRPC auth (JWT HS256, rahasia acak per jalan) + sesi Redis.
Keluaran per langkah: /out/<skenario>/<lengan>/NN_<langkah>.json (request + status + response NYATA) — bahan
fixture e2e FE. potret() = yang dibaca CW + cek Iron Laws (jurnal seimbang, AR == compute_ar_outstanding).
Hasil: PASS | FAIL | KNOWN (galat dikenal yang sedang diperbaiki; tak dihitung temuan baru)."""
import importlib, json, os, sys, time, uuid
from decimal import Decimal as D

import httpx

sys.path.insert(0, "/h")
import journey_pagar as PAGAR   # noqa: E402

T = "kaos-biru-konveksi"
OWNER, KOLAB = "0bccdb25-fdf0-4e99-9024-b9a20846f76c", "a2d3129a-91a6-4144-8b3b-39f149790d87"
PELANGGAN, NAMA = "d2d6d24b-1de1-4be0-8ef8-4cd057ca5617", "Toko Merdeka"
BARANG, GUDANG, KAS = ("35175eb3-143e-456f-a3b1-1c68bf13c683", "ba2a8f6d-fc58-4498-a080-40aa7fe88343",
                       "28ec7814-f10e-4688-af93-ef82f1b6f71f")
ROUTER_A = (("sales_orders", "/api/sales-orders"), ("sales_invoices", "/api/sales-invoices"),
            ("customer_deposits", "/api/customer-deposits"), ("receive_payments", "/api/receive-payments"),
            ("customers", "/api/customers"), ("tenant_profile", "/api/tenant"), ("proformas", "/api/proformas"),
            ("credit_notes", "/api/credit-notes"))


class Journey:
    def __init__(self, skenario: str, lengan: str, dikenal: dict):
        self.lengan, self.dikenal = lengan, dikenal
        # B meniru prod: satu salinan modul lewat nama paket prod (dua salinan = 403/500 palsu)
        self.M = "backend.api_gateway.app" if lengan == "B" else "app"
        sys.path.insert(0, "/app" if lengan == "B" else "/wt/backend/api_gateway")
        self.out = f"/out/{skenario}/{lengan}"
        os.makedirs(self.out, exist_ok=True)
        self.hasil, self.no, self._ls = [], 0, None

    def mod(self, nama):
        return importlib.import_module(f"{self.M}.{nama}")

    async def buka(self):
        self.pool = await self.mod("services.db_pool").get_db_pool()
        async with self.pool.acquire() as c:
            print("PAGAR hijau", await PAGAR.periksa(c, f"lengan {self.lengan}"))
            self.mulai = await c.fetchval("select clock_timestamp()")
            self.hari = await c.fetchval("select tanggal_bisnis($1)", T)
        app = self._app()
        if self.lengan == "B":     # startup NYATA (PolicyEngineClient dll.)
            self._ls = app.router.lifespan_context(app)
            await self._ls.__aenter__()
            async with self.pool.acquire() as c:
                await PAGAR.periksa(c, "sesudah startup")
        self.cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                    base_url="http://journey", timeout=120)

    def _app(self):
        if self.lengan == "B":
            import jwt as pyjwt
            auth_client = self.mod("services.auth_instance").auth_client
            sesi = self.mod("services.session_manager").session_manager

            async def validate(token):
                try:
                    d = pyjwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
                except Exception:
                    return {"valid": False}
                return {"valid": True, "user_id": d["sub"], "tenant_id": d["tenant_id"], "role": d.get("role", "USER"),
                        "email": None, "username": None, "device_id": d.get("device_id"),
                        "device_type": d.get("device_type")}
            auth_client.validate_token = validate
            sesi.is_session_valid = lambda *a, **k: True
            return self.mod("main").app
        from fastapi import FastAPI, Request
        app = FastAPI()

        @app.middleware("http")
        async def pengguna(request: Request, call_next):
            u = request.headers.get("x-uji-user")
            if u:
                request.state.user = {"user_id": u, "tenant_id": T, "role": "OWNER" if u == OWNER else "USER"}
            return await call_next(request)
        for nama, prefiks in ROUTER_A:
            try:
                app.include_router(self.mod(f"routers.{nama}").router, prefix=prefiks)
            except ModuleNotFoundError:
                print("lengan A: router tak ada, dilewati:", nama)
        # main.py juga memasang router KEDUA proformas di bawah /api/sales-orders ({order_id}/proformas)
        app.include_router(self.mod("routers.proformas").so_router, prefix="/api/sales-orders")
        return app

    def _hdr(self, user):
        if self.lengan == "B":
            import jwt as pyjwt
            tok = pyjwt.encode({"sub": user, "tenant_id": T, "role": "OWNER" if user == OWNER else "USER",
                                "device_id": "journey", "device_type": "web", "exp": int(time.time()) + 3600},
                               os.environ["JWT_SECRET"], algorithm="HS256")
            return {"Authorization": f"Bearer {tok}"}
        return {"x-uji-user": user}

    def _catat(self, nama, rek):
        self.no += 1
        with open(f"{self.out}/{self.no:02d}_{nama}.json", "w") as f:
            json.dump({"no": self.no, **rek}, f, indent=1, default=str, ensure_ascii=False)
        self.hasil.append((self.no, nama, rek["hasil"], rek.get("status")))

    def gagal(self, nama, pesan):
        """Langkah yang tak bisa dijalankan = FAIL tercatat (lewat senyap = hijau palsu)."""
        self._catat(nama, {"langkah": nama, "hasil": "FAIL", "pesan": pesan})
        print(f"{self.no:02d} FAIL  {nama}: {pesan}")

    async def langkah(self, nama, metode, jalur, body=None, user=OWNER, headers=None, harap=(200, 201), kenal=None):
        h = self._hdr(user)
        h.update(headers or {})
        r = await self.cl.request(metode, jalur, json=body, headers=h)
        try:
            isi = r.json()
        except Exception:
            isi = r.text[:2000]
        ok = r.status_code in harap
        hasil = "PASS" if ok else ("KNOWN" if kenal else "FAIL")
        rek = {"langkah": nama, "hasil": hasil, "status": r.status_code, "harap": list(harap),
               "request": {"method": metode, "path": jalur, "body": body,
                           "user": "owner" if user == OWNER else "collaborator"}, "response": isi}
        if kenal and not ok:
            rek["dikenal"] = self.dikenal[kenal]
        self._catat(nama, rek)
        print(f"{self.no:02d} {hasil:5} {r.status_code} {metode} {jalur} :: {nama}")
        return r.status_code, isi

    async def faktur_so(self, so_id, kecuali=()):
        async with self.pool.acquire() as c:
            rows = await c.fetch("select id from sales_invoices where tenant_id=$1 and sales_order_id=$2 "
                                 "order by created_at", T, uuid.UUID(so_id))
        return [str(r["id"]) for r in rows if str(r["id"]) not in kecuali]

    async def potret(self, nama, so_id=None, inv_ids=()):
        det, ring = {}, {}
        if so_id:
            _, det = await self.langkah(f"{nama}__detail_so", "GET", f"/api/sales-orders/{so_id}")
            _, ring = await self.langkah(f"{nama}__summary_so", "GET", "/api/sales-orders/summary")
        async with self.pool.acquire() as c:
            jur = await c.fetch(
                """select je.journal_number, je.source_type, je.status, je.total_debit, je.total_credit,
                          coalesce(sum(jl.debit),0) sd, coalesce(sum(jl.credit),0) sk
                   from journal_entries je left join journal_lines jl on jl.journal_id=je.id
                   where je.tenant_id=$1 and je.created_at >= $2 group by je.id order by je.created_at""",
                T, self.mulai)
            ar = {str(r["invoice_id"]): D(str(r["outstanding"])) for r in await c.fetch(
                "select invoice_id, outstanding from compute_ar_outstanding($1)", T)} if inv_ids else {}
        timpang = [dict(j) for j in jur if not (j["total_debit"] == j["total_credit"] == j["sd"] == j["sk"])]
        inv = []
        for i in inv_ids:
            _, si = await self.langkah(f"{nama}__detail_si", "GET", f"/api/sales-invoices/{i}")
            d = (si or {}).get("data") or {} if isinstance(si, dict) else {}
            st_si = d.get("status")
            due = D(str(d["amount_due"])) if d.get("amount_due") is not None else None
            # faktur DRAF/VOID bukan piutang: compute_ar_outstanding WAJIB 0 (amount_due API = nominal dokumen)
            sama = (ar.get(i, D(0)) == 0) if st_si in ("draft", "void", "voided") else (due == ar.get(i, D(0)))
            inv.append({"invoice_id": i, "status": st_si, "amount_due_api": str(due),
                        "compute_ar_outstanding": str(ar.get(i, D(0))), "sama": sama})
        so = (det or {}).get("data") or {} if isinstance(det, dict) else {}
        s = (ring or {}).get("data") or {} if isinstance(ring, dict) else {}
        p = {"so_status": so.get("status"), "payment_summary": so.get("payment_summary"),
             "shipped_qty": so.get("shipped_qty"), "invoiced_qty": so.get("invoiced_qty"),
             "baris": [{k: b.get(k) for k in ("quantity", "fulfilled_qty", "unfulfilled_qty", "unfulfilled_value",
                                               "quantity_invoiced")} for b in so.get("items") or []],
             "penanda": {k: so.get(k) for k in so if k.startswith(("has_", "is_", "draft_"))},
             "summary": {k: s.get(k) for k in ("unshipped_value", "unshipped_count", "fulfillment_count",
                                                "uninvoiced_value", "uninvoiced_count")},
             "jurnal_baru": [(j["journal_number"], j["source_type"], j["status"], str(j["total_debit"])) for j in jur],
             "jurnal_timpang": timpang, "invoice_ar": inv}
        ok = not timpang and all(x["sama"] for x in inv) and all(j["status"] == "POSTED" for j in jur)
        self._catat(f"{nama}__POTRET", {"langkah": nama, "hasil": "PASS" if ok else "FAIL", **p})
        print(f"{self.no:02d} {'PASS' if ok else 'FAIL':5} potret {nama}: status={p['so_status']} "
              f"jurnal={len(jur)} timpang={len(timpang)} ar={[(x['status'], x['sama']) for x in inv]}")
        return p

    async def tutup(self):
        await self.cl.aclose()
        if self._ls is not None:
            await self._ls.__aexit__(None, None, None)
        ring = {"lengan": self.lengan, "PASS": sum(1 for h in self.hasil if h[2] == "PASS"),
                "FAIL": [h for h in self.hasil if h[2] == "FAIL"], "KNOWN": [h for h in self.hasil if h[2] == "KNOWN"]}
        with open(f"{self.out}/RINGKAS.json", "w") as f:
            json.dump(ring, f, indent=1, default=str)
        print("RINGKAS", json.dumps(ring, default=str))
        return ring
