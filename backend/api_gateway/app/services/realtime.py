"""Realtime hub — Tahap 1 pembaruan-instan.

Satu koneksi asyncpg khusus (LUAR pool) LISTEN 'doc_changed' + 'membership_changed'.
Coalesce per (tenant,tbl,id) ~150ms. Fan-out ke koneksi SSE per-worker (multi-worker:
tiap worker LISTEN sendiri; Redis TIDAK diperlukan). Reconnect + keepalib (deteksi half-open) +
resync pasca-(re)connect. Payload event minimal {tbl,id,op}.
"""
import asyncio
import json
import logging
import uuid as _uuid
from typing import Dict, Optional, Set

import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)

# tbl -> modul autz. SATU SUMBER; harus cocok PROTECTED_ROUTES (permission_middleware).
# Tahap 1: sales_invoices saja.
TBL_MODULE: Dict[str, str] = {
    "sales_invoices": "sales_invoice",
    "bills": "bill",                    # G1 (Tahap 2) -> normalize BILL
    "bill_payments_v2": "send_payment",  # G1 (Tahap 2) -> normalize PAYMENT
    "sales_orders": "sales_order",       # G2 (Tahap 2) -> normalize SALES_ORDER
    "receive_payments": "receive_payment",   # G3 -> normalize RECEIPT
    "customer_deposits": "customer_deposit",  # G3 -> normalize CUSTOMER_DEPOSIT
    "bank_transactions": "kas_bank",         # G4 -> normalize KAS_BANK
    "bank_transfers": "kas_bank",            # G4 -> normalize KAS_BANK
    "credit_notes": "credit_note",           # G5 -> normalize CREDIT_NOTE
    "quotes": "quote",                       # G6 -> normalize QUOTE
    # G7 whole-app remainder (24)
    "products": "item",
    "customers": "customer",
    "vendors": "supplier",
    "chart_of_accounts": "chart_of_accounts",
    "bank_accounts": "kas_bank",
    "warehouses": "warehouse",
    "product_units": "unit",
    "tax_codes": "tax",
    "fiscal_periods": "period",
    "journal_entries": "journal",
    "expenses": "expense",
    "vendor_credits": "debit_note",
    "vendor_deposits": "vendor_deposit",
    "stock_adjustments": "stock_adjust",
    "employees": "employee",
    "salary_components": "salary_component",
    "pay_groups": "pay_group",
    "payroll_runs": "payroll",
    "production_orders": "work_order",
    "work_centers": "work_center",
    "bill_of_materials": "bom",
    "user_tenant_roles": "team_management",
    "tenant_config": "tenant_settings",
    "payment_requests": "payment_request",
}

# #41: kredensial dari env (DB_PASSWORD via config.settings) -- sama dengan pool utama.
_DB = settings.get_db_config()

COALESCE_S = 0.15
KEEPALIVE_S = 25
BULK_THRESHOLD = 20  # >threshold event utk 1 (tenant,tbl) dlm satu flush -> 1 bulk_changed


class ClientConn:
    __slots__ = ("id", "tenant_id", "user_id", "allowed_modules", "exp", "queue")

    def __init__(self, tenant_id: str, user_id: str, allowed_modules: Set[str], exp):
        self.id = str(_uuid.uuid4())
        self.tenant_id = tenant_id
        self.user_id = str(user_id)
        self.allowed_modules: Set[str] = allowed_modules
        self.exp = exp
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=500)


class RealtimeHub:
    def __init__(self):
        self.connections: Dict[str, ClientConn] = {}
        self._listen_conn: Optional[asyncpg.Connection] = None
        self._tasks: list = []
        self._pending: Dict[tuple, dict] = {}
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._listen_loop()),
            asyncio.create_task(self._flush_loop()),
        ]
        logger.info("RealtimeHub started")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        if self._listen_conn:
            try:
                await self._listen_conn.close()
            except Exception:
                pass

    def register(self, c: ClientConn):
        self.connections[c.id] = c

    def unregister(self, cid: str):
        self.connections.pop(cid, None)

    # ---- NOTIFY callbacks (dipanggil sinkron oleh asyncpg di loop) ----
    def _on_doc(self, conn, pid, channel, payload):
        try:
            ev = json.loads(payload)
        except Exception:
            return
        # coalesce: kunci (tenant,tbl,id); op terakhir menang
        self._pending[(ev.get("tenant_id"), ev.get("tbl"), ev.get("id"))] = ev

    def _on_membership(self, conn, pid, channel, payload):
        try:
            ev = json.loads(payload)
        except Exception:
            return
        uid = str(ev.get("user_id"))
        tid = ev.get("tenant_id")
        for c in list(self.connections.values()):
            if c.user_id == uid and c.tenant_id == tid:
                self._safe_put(c, {"type": "recheck"})

    async def _listen_loop(self):
        backoff = 1
        while self._running:
            dead = asyncio.Event()
            try:
                self._listen_conn = await asyncpg.connect(
                    **_DB, server_settings={"application_name": "mh_realtime_listen"}
                )
                # deteksi SEGERA koneksi mati (kill/putus), tak menunggu keepalive
                self._listen_conn.add_termination_listener(lambda con: dead.set())
                await self._listen_conn.add_listener("doc_changed", self._on_doc)
                await self._listen_conn.add_listener("membership_changed", self._on_membership)
                logger.info("RealtimeHub: LISTEN established")
                # resync semua klien pasca-(re)connect: notifikasi saat putus tak terulang
                for c in list(self.connections.values()):
                    self._safe_put(c, {"type": "resync"})
                backoff = 1
                while self._running and not dead.is_set():
                    try:
                        await asyncio.wait_for(dead.wait(), timeout=KEEPALIVE_S)
                    except asyncio.TimeoutError:
                        # keepalive aktif: deteksi TCP half-open (reconnect kalau gagal)
                        await self._listen_conn.execute("SELECT 1")
                if dead.is_set():
                    raise ConnectionError("listen connection terminated")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"RealtimeHub listen error: {e}; reconnect in {backoff}s")
                try:
                    if self._listen_conn:
                        await self._listen_conn.close()
                except Exception:
                    pass
                self._listen_conn = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _flush_loop(self):
        while self._running:
            try:
                await asyncio.sleep(COALESCE_S)
                if not self._pending:
                    continue
                batch = list(self._pending.values())
                self._pending.clear()
                # kelompokkan per (tenant,tbl): burst besar (mis. import/posting massal)
                # -> 1 bulk_changed, bukan ratusan event (gerbang 7). Kecil -> per-dokumen.
                groups: Dict[tuple, list] = {}
                for ev in batch:
                    groups.setdefault((ev.get("tenant_id"), ev.get("tbl")), []).append(ev)
                for (tenant, tbl), evs in groups.items():
                    if len(evs) > BULK_THRESHOLD:
                        self._route_bulk(tenant, tbl)
                    else:
                        for ev in evs:
                            self._route(ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"RealtimeHub flush error: {e}")

    def _emit(self, tenant: str, tbl: str, out: dict):
        module = TBL_MODULE.get(tbl)
        for c in list(self.connections.values()):
            if c.tenant_id != tenant:
                continue
            # FAIL-CLOSED authz: an UNMAPPED tbl (module is None) must NOT broadcast
            # unfiltered -- that leaked a doc-changed metadata event to users without
            # READ on that module. Drop unmapped, and drop when the module is not in
            # the connection allowed set. A new tbl stays dark until TBL_MODULE maps it.
            if module is None or module not in c.allowed_modules:
                continue
            self._safe_put(c, out)

    def _route(self, ev: dict):
        self._emit(
            ev.get("tenant_id"),
            ev.get("tbl"),
            {"type": "doc_changed", "tbl": ev.get("tbl"), "id": ev.get("id"), "op": ev.get("op")},
        )

    def _route_bulk(self, tenant: str, tbl: str):
        self._emit(tenant, tbl, {"type": "bulk_changed", "tbl": tbl})

    def _safe_put(self, c: ClientConn, msg: dict):
        try:
            c.queue.put_nowait(msg)
        except asyncio.QueueFull:
            # klien lambat: buang tertua, minta resync (jangan blokir hub)
            try:
                c.queue.get_nowait()
                c.queue.put_nowait({"type": "resync"})
            except Exception:
                pass


hub = RealtimeHub()
