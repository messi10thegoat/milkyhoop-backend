"""
Entity Resolver — Compiler Pipeline Stage 2.

Resolves extracted entity names to database IDs.
Code-driven: parallel DB queries, fuzzy matching, clarification generation.
Zero LLM calls.

CRITICAL: Check DB column names before writing queries.
customers.nama (NOT name!), products.nama_produk (NOT name!),
customers.id = UUID (terverifikasi [SQL] 2026-08-09) — bukan varchar.
Tipe customer_id TIDAK seragam: uuid di customers.id / sales_invoices /
receive_payments; varchar di credit_notes / customer_deposits. Cek
information_schema per tabel sebelum bind; jangan menyamaratakan.
bank_accounts.coa_id (BUKAN chart_of_account_id!).
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("unified_agent.entity_resolver")

# G1 (T97): ambang pg_trgm untuk memutuskan apakah sebuah token yang DIBUANG di
# Step 2b masih punya "tetangga dekat" di master tenant (= salah ketik) atau
# tidak sama sekali (= token asing, barangnya memang tak ada di master).
_AMBANG_TOKEN_ASING = 0.25

# Token-token pembanding = kata-kata nama_produk + item_code + sku, per tenant.
_SQL_TETANGGA_DEKAT = """
    SELECT 1
      FROM products p
      CROSS JOIN LATERAL (
        SELECT unnest(
            regexp_split_to_array(lower(p.nama_produk), '\\s+')
            || ARRAY[lower(coalesce(p.item_code, '')),
                     lower(coalesce(p.sku, ''))]
        ) AS w
      ) tk
     WHERE p.tenant_id = $1
       AND p.status = 'active'
       AND tk.w <> ''
       AND similarity(tk.w, $2::text) >= $3::real
     LIMIT 1
"""


# T196 (A) — NORMALISASI TANDA BACA, HANYA untuk pencocokan EXACT dan untuk
# saringan teks mentah (C). SENGAJA TIDAK dipakai untuk memperlebar query ILIKE:
# normalisasi yang MEMPERLEBAR menaikkan risiko tabrakan; yang MENYEMPITKAN tidak.
# Terukur: nol tabrakan normalisasi di seluruh milkydb (65 produk, 2 tenant).
# POLA ditulis SEKALI dan dipakai di DUA sisi (Python `re` dan `regexp_replace`
# Postgres). Kalau disalin, dua salinan akan menyimpang diam-diam dan Step 0
# akan berhenti setara dengan _norm_cocok tanpa satu pun tes yang merah.
_POLA_TANDA_BACA = r"[()\[\]\-+.,/_&:;]+"
_POLA_SPASI = r"\s+"
_RE_TANDA_BACA = re.compile(_POLA_TANDA_BACA)
_RE_SPASI = re.compile(_POLA_SPASI)

# T196 STEP 0 — kesamaan PENUH atas nama yang ternormalisasi, dihitung di DB
# dengan pola yang SAMA PERSIS dengan _norm_cocok.
_SQL_EXACT_TERNORMALISASI = f"""
    SELECT id, nama_produk, sales_price_amount, purchase_price_amount, item_type
      FROM products
     WHERE tenant_id = $1
       AND status = 'active' AND deleted_at IS NULL
       AND regexp_replace(
             regexp_replace(lower(nama_produk), '{_POLA_TANDA_BACA}', ' ', 'g'),
             '{_POLA_SPASI}', ' ', 'g') = $2
     LIMIT 2
"""


def _norm_cocok(t: str) -> str:
    """Turunkan teks ke bentuk banding: tanda baca -> spasi, spasi rapat, huruf kecil."""
    return _RE_SPASI.sub(" ", _RE_TANDA_BACA.sub(" ", (t or "").lower())).strip()


def is_untrusted_id_field(key: str) -> bool:
    """True bila `key` adalah field ID yang TIDAK boleh diambil dari keluaran LLM.

    Iron Law 1 hardening. ID hanya boleh berasal dari resolver (lookup DB) atau
    dari input user — tak pernah dari generasi model. Stage-2 LLM terbukti
    mengisi `customer_id` dengan NAMA pelanggan (dogfood 2026-08-09), yang lolos
    sampai asyncpg dan meledak sebagai 500.

    SATU definisi dipakai bersama oleh setiap titik tempat entities hasil LLM
    bertemu payload. Kalau logikanya disalin, dua salinan akan menyimpang.
    """
    return key == "id" or key.endswith("_id") or key.endswith("_uuid")


# dok. 79 M1g — status internal jangan pernah sampai ke layar apa adanya.
_STATUS_ID = {
    "draft": "draf",
    "sent": "terkirim",
    "viewed": "sudah dilihat",
    "accepted": "diterima",
    "declined": "ditolak",
    "expired": "kedaluwarsa",
    "converted": "sudah dikonversi",
    "void": "dibatalkan",
    "cancelled": "dibatalkan",
}


@dataclass
class ResolvedEntity:
    """Single resolved entity."""

    entity_type: str
    entity_id: str
    entity_name: str
    confidence: float
    candidates: list = field(default_factory=list)
    # G1 (T97): kandidat ADA tapi diperoleh lewat jalur yang tak dapat
    # dipercaya (mis. Step 2b melonggarkan token ASING). Kandidat boleh
    # ditawarkan, TAPI tak boleh dipakai diam-diam, dan user WAJIB diberi
    # jalan keluar ("Bukan salah satu dari ini").
    low_trust: bool = False


@dataclass
class ResolutionResult:
    """Result of entity resolution."""

    resolved: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    clarifications: list = field(default_factory=list)
    needs_clarification: bool = False
    payload: dict = field(default_factory=dict)


class EntityResolver:
    """Resolve entity names to DB IDs and construct final payload."""

    def __init__(self, db_pool, tenant_id: str):
        self.db = db_pool
        self.tenant_id = tenant_id

    async def resolve_and_complete(
        self,
        intent: str,
        entities: dict,
        modifiers: list = None,
        memory_state: dict = None,
        system_defaults: dict = None,
        entity_graph: dict = None,
        action_memory_suggestion: dict = None,
        user_text: str = "",
        session_id: str = "",
    ) -> ResolutionResult:
        modifiers = modifiers or []
        memory_state = memory_state or {}
        # K0: satu-satunya tempat yang menjamin system_defaults["date"] ADA dan
        # memakai zona TENANT, bukan zona server. Invarian ditegakkan di SINI
        # supaya konsumennya (lihat "defaults hoist" di bawah) tak perlu cabang
        # cadangan yang tak pernah tercapai.
        if not system_defaults or not system_defaults.get("date"):
            from ...utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

            system_defaults = {
                **(system_defaults or {}),
                "date": (await tanggal_dokumen(self.db, self.tenant_id)).isoformat(),
            }

        result = ResolutionResult()

        # ── Aksi atas dokumen yang sudah ada (dok. 79) ────────────────────
        # Jalur pendek dan MANDIRI: aksi ini tidak membangun dokumen dari
        # entitas hasil LLM, ia menunjuk SATU dokumen yang sudah ada. Nol
        # nominal, nol tanggal, nol nama dari model — hanya nomor dokumen
        # yang diketik user, diubah jadi UUID oleh DB.
        from .direct_action_registry import DOCUMENT_ACTIONS_BY_KEY

        _aksi_dok = DOCUMENT_ACTIONS_BY_KEY.get(intent)
        if _aksi_dok is not None:
            return await self._resolve_aksi_dokumen(_aksi_dok, entities)

        # Step A: Resolve extracted entities (parallel DB queries)
        # Skip entity resolution for create intents where fields are TEXT, not references.
        # e.g. create_vendor: vendor_name/bank_name are the new vendor info, not lookups.
        _skip_vendor_resolve = intent in ("create_vendor",)
        _skip_customer_resolve = intent in ("create_customer",)
        _skip_bank_resolve = intent in ("create_vendor", "create_customer")
        resolve_tasks = []
        if entities.get("customer_name") and not _skip_customer_resolve:
            resolve_tasks.append(self._resolve_customer(entities["customer_name"]))
        if entities.get("vendor_name") and not _skip_vendor_resolve:
            resolve_tasks.append(self._resolve_vendor(entities["vendor_name"]))
        if (
            entities.get("item_name")
            and not intent.startswith("create_item")
            and intent != "create_expense"
        ):
            resolve_tasks.append(self._resolve_item(entities["item_name"], user_text))
        if entities.get("bank_name") and not _skip_bank_resolve:
            resolve_tasks.append(self._resolve_bank_account(entities["bank_name"]))
        if entities.get("warehouse_name"):
            resolve_tasks.append(self._resolve_warehouse(entities["warehouse_name"]))
        if entities.get("invoice_number"):
            resolve_tasks.append(self._resolve_invoice(entities["invoice_number"]))
        if entities.get("bill_number"):
            resolve_tasks.append(self._resolve_bill(entities["bill_number"]))
        if entities.get("account_name"):
            resolve_tasks.append(self._resolve_account(entities["account_name"]))
        if entities.get("work_order_number"):
            resolve_tasks.append(
                self._resolve_work_order(entities["work_order_number"])
            )
        if entities.get("bom_code"):
            resolve_tasks.append(self._resolve_bom(entities["bom_code"]))
        if entities.get("work_center_name"):
            resolve_tasks.append(
                self._resolve_work_center(entities["work_center_name"])
            )

        if resolve_tasks:
            resolved_entities = await asyncio.gather(
                *resolve_tasks, return_exceptions=True
            )
            for res in resolved_entities:
                if isinstance(res, Exception):
                    logger.warning("[RESOLVE] Entity resolution failed: %s", res)
                    continue
                if res is None:
                    continue
                # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: single low-sim fuzzy must NOT auto-pick
                if res.confidence >= 0.85 and len(res.candidates) <= 1:
                    result.resolved[res.entity_type] = res
                elif len(res.candidates) > 1:
                    result.resolved[res.entity_type] = res
                    candidates_str = ", ".join(
                        f"{i+1}) {c['name']}" for i, c in enumerate(res.candidates[:5])
                    )
                    result.clarifications.append(
                        f"Saya temukan {len(res.candidates)} {res.entity_type}: {candidates_str}. Yang mana?"
                    )
                    result.needs_clarification = True
                elif (
                    res.candidates
                    and len(res.candidates) == 1
                    and 0.5 <= res.confidence < 0.85
                ):
                    # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: single fuzzy guess — ask before posting
                    result.resolved[res.entity_type] = res
                    only_name = res.candidates[0]["name"]
                    result.clarifications.append(
                        f"{res.entity_type.capitalize()} '{res.entity_name}' tidak ditemukan persis. Maksud Anda *{only_name}*? (atau buat baru)"
                    )
                    result.needs_clarification = True
                elif res.confidence < 0.5:
                    result.missing.append(res.entity_type)

        # Step A.5: Graph-based resolution for implicit references
        if entity_graph:
            from .entity_graph import (
                get_last_node,
                get_focus,
                get_by_ordinal,
                traverse,
                _ensure_graph,
            )

            graph = _ensure_graph(entity_graph)
            _ltxt = (user_text or "").lower()
            _sid = (session_id or "")[:8]

            # Existing pronoun-style fallback for customer
            if not entities.get("customer_name") and "customer" not in result.resolved:
                focus = get_focus(graph)
                if focus and focus.get("type") == "customer":
                    result.resolved["customer"] = ResolvedEntity(
                        entity_type="customer",
                        entity_id=focus["id"],
                        entity_name=focus["name"],
                        confidence=0.9,
                    )
                else:
                    last_cust = get_last_node(graph, "customer")
                    if last_cust:
                        result.resolved["customer"] = ResolvedEntity(
                            entity_type="customer",
                            entity_id=last_cust["id"],
                            entity_name=last_cust["name"],
                            confidence=0.85,
                        )
            if not entities.get("vendor_name") and "vendor" not in result.resolved:
                last_vendor = (
                    get_last_node(graph, "vendor") if graph.get("nodes") else None
                )
                if last_vendor:
                    result.resolved["vendor"] = ResolvedEntity(
                        entity_type="vendor",
                        entity_id=last_vendor["id"],
                        entity_name=last_vendor["name"],
                        confidence=0.85,
                    )

            # B2 Site 1 — pronoun-triggered traversal: "dia"/"itu"/"tadi"/"tersebut"
            # + verb like "faktur"/"tagihan"/"invoice" -> direct_relation (Site 3).
            # Otherwise plain pronoun stays as Site 1 (no-op here; existing logic
            # above already resolved the entity; traverse is extra accelerator).
            _PRONOUN_TOKENS = (" dia", " itu", " tadi", " tersebut", "nya ")
            _has_pronoun = any(tok in f" {_ltxt} " for tok in _PRONOUN_TOKENS)
            _wants_invoice = any(w in _ltxt for w in ("faktur", "invoice"))
            _wants_bill = any(w in _ltxt for w in ("tagihan", "bill"))

            # Determine "from" node for traversal: prefer already-resolved focus/customer/vendor
            _from_node = get_focus(graph)
            if not _from_node:
                _from_node = get_last_node(graph, "customer") or get_last_node(
                    graph, "vendor"
                )

            # B2 Site 3 — direct_relation: pronoun + document noun
            if _has_pronoun and _from_node and (_wants_invoice or _wants_bill):
                _target_type = "invoice" if _wants_invoice else "bill"
                try:
                    hits = traverse(
                        graph,
                        _from_node["_key"],
                        max_depth=1,
                        edge_type="owns",
                        node_type_filter=_target_type,
                    )
                except (KeyError, TypeError):
                    logger.error(
                        "graph_traverse_failed session=%s from=%s",
                        _sid,
                        _from_node.get("_key"),
                        exc_info=True,
                    )
                    hits = []
                logger.info(
                    "graph_traverse session=%s type=direct_relation depth=1 from=%s edge=owns hits=%d",
                    _sid,
                    _from_node.get("_key"),
                    len(hits),
                )
                if hits:
                    # Sort by ts desc — "terakhir"
                    hits.sort(key=lambda n: n.get("ts", 0), reverse=True)
                    pick = hits[0]
                    _field = "invoice" if _target_type == "invoice" else "bill"
                    if _field not in result.resolved:
                        result.resolved[_field] = ResolvedEntity(
                            entity_type=_field,
                            entity_id=pick["id"],
                            entity_name=pick.get("name", ""),
                            confidence=0.85,
                        )

            # B2 Site 2 — ordinal_relation: "customer pertama hutangnya berapa?"
            _ORDINALS = {
                1: ("pertama", "nomor 1", "no 1", "no. 1"),
                2: ("kedua", "nomor 2", "no 2", "no. 2"),
                3: ("ketiga", "nomor 3", "no 3", "no. 3"),
            }
            _ord_idx = None
            for idx, kws in _ORDINALS.items():
                if any(k in _ltxt for k in kws):
                    _ord_idx = idx
                    break
            if _ord_idx and ("customer" in _ltxt or "pelanggan" in _ltxt):
                ord_node = get_by_ordinal(graph, "customer", _ord_idx)
                if ord_node:
                    if "customer" not in result.resolved:
                        result.resolved["customer"] = ResolvedEntity(
                            entity_type="customer",
                            entity_id=ord_node["id"],
                            entity_name=ord_node.get("name", ""),
                            confidence=0.85,
                        )
                    if any(
                        k in _ltxt for k in ("hutang", "piutang", "tagihan", "faktur")
                    ):
                        try:
                            hits = traverse(
                                graph,
                                ord_node["_key"],
                                max_depth=1,
                                edge_type="owns",
                                node_type_filter="invoice",
                            )
                        except (KeyError, TypeError):
                            logger.error(
                                "graph_traverse_failed session=%s from=%s",
                                _sid,
                                ord_node.get("_key"),
                                exc_info=True,
                            )
                            hits = []
                        logger.info(
                            "graph_traverse session=%s type=ordinal_relation depth=1 from=%s edge=owns hits=%d",
                            _sid,
                            ord_node.get("_key"),
                            len(hits),
                        )
            elif _ord_idx and ("vendor" in _ltxt or "pemasok" in _ltxt):
                ord_node = get_by_ordinal(graph, "vendor", _ord_idx)
                if ord_node and "vendor" not in result.resolved:
                    result.resolved["vendor"] = ResolvedEntity(
                        entity_type="vendor",
                        entity_id=ord_node["id"],
                        entity_name=ord_node.get("name", ""),
                        confidence=0.85,
                    )

        # Step B: Complete from memory + defaults (3-source merge)
        result.payload = self._build_payload(
            intent,
            entities,
            result.resolved,
            memory_state,
            system_defaults,
            action_memory_suggestion=action_memory_suggestion,
        )

        # Step B.5: Auto-resolve account for create_expense (keyword inference)
        if (
            intent == "create_expense"
            and "account" not in result.resolved
            and not result.payload.get("account_id")
        ):
            acct_name = result.payload.get("account_name", "")
            desc = result.payload.get("description", "")
            # Strategy 1: user explicitly said account name
            if acct_name:
                acct_res = await self._resolve_account(acct_name)
                if acct_res and acct_res.confidence >= 0.7:
                    result.resolved["account"] = acct_res
                    result.payload["account_id"] = acct_res.entity_id
                    result.payload["account_name"] = acct_res.entity_name
            # Strategy 2: keyword inference from description
            if not result.payload.get("account_id") and desc:
                _EXPENSE_KW = {
                    "listrik": "Beban Listrik",
                    "air pdam": "Beban Air",
                    "telepon": "Beban Telepon",
                    "internet": "Beban Telepon & Internet",
                    "wifi": "Beban Telepon & Internet",
                    "pulsa": "Beban Telepon & Internet",
                    "telefon": "Beban Telepon & Internet",
                    "telpon": "Beban Telepon & Internet",
                    "sewa": "Beban Sewa",
                    "gaji": "Beban Gaji",
                    "transport": "Beban Transportasi",
                    "bensin": "Beban Transportasi",
                    "parkir": "Beban Transportasi",
                    "tol": "Beban Transportasi",
                    "ojek": "Beban Transportasi",
                    "grab": "Beban Transportasi",
                    "servis": "Beban Pemeliharaan",
                    "service": "Beban Pemeliharaan",
                    "reparasi": "Beban Pemeliharaan",
                    "perbaikan": "Beban Pemeliharaan",
                    "maintenance": "Beban Pemeliharaan",
                    "perawatan": "Beban Pemeliharaan",
                    "makan": "Beban Makan & Minum",
                    "minum": "Beban Makan & Minum",
                    "snack": "Beban Makan & Minum",
                    "catering": "Beban Makan & Minum",
                    "konsumsi": "Beban Makan & Minum",
                    "atk": "Beban Perlengkapan Kantor",
                    "alat tulis": "Beban Perlengkapan Kantor",
                    "kertas": "Beban Perlengkapan Kantor",
                    "printer": "Beban Perlengkapan Kantor",
                    "asuransi": "Beban Asuransi",
                    "pajak": "Beban Pajak",
                    "admin bank": "Biaya Admin Bank",
                    "biaya bank": "Biaya Admin Bank",
                }
                desc_lower = desc.lower()
                matched = None
                for kw, acct in _EXPENSE_KW.items():
                    if kw in desc_lower:
                        matched = acct
                        break
                if not matched:
                    matched = "Beban Lain-lain"
                acct_res = await self._resolve_account(matched)
                if acct_res and acct_res.confidence >= 0.5:
                    result.resolved["account"] = acct_res
                    result.payload["account_id"] = acct_res.entity_id
                    result.payload["account_name"] = acct_res.entity_name

        # ── FIX_DOGFOOD_RECEIVEPAY_RESOLVE (2026-06-09): no-hint bank 3-tier ──
        # Payment intents need a bank/cash account (hidden required field), but
        # the user often states the METHOD ("lewat transfer bank") not a bank
        # NAME, so _resolve_bank_account never ran and `bank_account` is absent
        # from resolved. Without this, validation reports the hidden field
        # missing -> the old code leaked "Bank Account ID" to the user. Here we
        # apply the proper 3-tier resolution: fetch ACTIVE bank/cash accounts;
        # exactly 1 -> auto-pick into payload; >1 -> populate resolved candidates
        # so the orchestrator's existing pills shortcut asks by NAME; 0 -> leave
        # unresolved (downstream surfaces a human ask, never an ID). READ-ONLY.
        _bank_payment_intents = (
            "create_receive_payment",
            "create_bill_payment",
        )
        if (
            intent in _bank_payment_intents
            and "bank_account" not in result.resolved
            and not result.payload.get("bank_account_id")
        ):
            try:
                _bank_rows = await self.db.fetch(
                    """SELECT id, account_name, bank_name
                       FROM bank_accounts
                       WHERE tenant_id = $1 AND is_active = true
                       ORDER BY account_name""",
                    self.tenant_id,
                )
                if _bank_rows:
                    _cands = [
                        {"id": str(r["id"]), "name": r["account_name"]}
                        for r in _bank_rows
                    ]
                    if len(_cands) == 1:
                        # Single active account -> auto-pick (tier 2 collapse).
                        result.resolved["bank_account"] = ResolvedEntity(
                            entity_type="bank_account",
                            entity_id=_cands[0]["id"],
                            entity_name=_cands[0]["name"],
                            confidence=1.0,
                            candidates=_cands,
                        )
                        result.payload["bank_account_id"] = _cands[0]["id"]
                        result.payload["bank_account_name"] = _cands[0]["name"]
                    else:
                        # >1 active accounts -> ambiguous, present pills by NAME.
                        result.resolved["bank_account"] = ResolvedEntity(
                            entity_type="bank_account",
                            entity_id=_cands[0]["id"],
                            entity_name=_cands[0]["name"],
                            confidence=0.7,
                            candidates=_cands,
                        )
                        result.payload.pop("bank_account_id", None)
                        result.payload.pop("bank_account_name", None)
                        result.needs_clarification = True
            except Exception as _bank_err:
                logger.warning(
                    "[RESOLVE] no-hint bank fallback failed (non-fatal): %s",
                    _bank_err,
                )

        # Step C: Check required fields
        from .direct_action_registry import (
            get_direct_action,
            validate_payload,
            apply_defaults,
            DIRECT_ACTIONS,
        )

        config = get_direct_action(intent)
        if config:
            # Pre-validate defaults hoist (fixes validate-then-enrich ordering bug).
            # Fills deterministic field defaults BEFORE validate_payload so required date
            # fields + FieldSpec defaults don't spuriously trigger needs_clarification.
            try:
                apply_defaults(intent, result.payload)
            except Exception as _e:
                logger.warning(f"apply_defaults failed for {intent}: {_e}")
            # K0 2026-08-12: dulu baris ini `datetime.now().strftime(...)` —
            # zona SERVER. Ia mengisi SETIAP FieldSpec bertipe date+required yang
            # masih kosong: 16 field di 15 aksi. Karena loopnya generik, nama
            # fieldnya tak pernah muncul sebagai literal, jadi `grep quote_date`
            # mengembalikan NOL dan penulisnya tak terlihat selama dua batch.
            #
            # Nilai yang BENAR sudah dioper ke fungsi ini sejak awal;
            # kodenya menghitung tanggalnya sendiri padahal jawabannya ada di
            # dalam jangkauan. Cukup memakainya.
            #
            # TANPA cadangan `or ...`: resolve_and_complete punya satu pemanggil
            # (orchestrator) yang selalu mengoper "date", dan normalisasi di awal
            # metode ini menjamin kuncinya ada. Cadangan di sini akan jadi cabang
            # yang tak pernah dieksekusi — dan cabang mati membusuk tanpa
            # ketahuan.
            #
            # Tujuan asli blok ini (2734fc18) TIDAK berubah: mengisi tanggal
            # sebelum validate_payload supaya bot tidak menanyakan "tanggal
            # berapa?" untuk dokumen yang tak menyebut tanggal.
            _today = system_defaults["date"]
            _cfg_full = DIRECT_ACTIONS.get(intent)
            if _cfg_full:
                for _f in _cfg_full.fields:
                    if (
                        getattr(_f, "field_type", None) == "date"
                        and getattr(_f, "required", False)
                        and not result.payload.get(_f.name)
                    ):
                        result.payload[_f.name] = _today
            is_valid, missing_fields = validate_payload(intent, result.payload)
            if not is_valid:
                result.missing.extend(missing_fields)
                # FIX_DOGFOOD_RECEIVEPAY_RESOLVE (2026-06-09): validate_payload now
                # returns ONLY user-facing labels (hidden/display_only excluded).
                # When the sole unresolved required fields are hidden IDs,
                # missing_fields is EMPTY but is_valid is False. Do NOT emit an
                # empty "Mohon lengkapi:" clarification (that produced the raw-ID
                # ask). Still flag needs_clarification so the orchestrator (bank
                # pills / name-unresolved branch) handles the hidden resolution.
                if missing_fields and not result.needs_clarification:
                    labels_str = ", ".join(missing_fields)
                    result.clarifications.append(f"Mohon lengkapi: {labels_str}")
                    result.needs_clarification = True
                elif not missing_fields:
                    # hidden-only missing -> ensure downstream resolves, not asks ID
                    result.needs_clarification = True

        return result

    def _build_payload(
        self,
        intent,
        entities,
        resolved,
        memory_state,
        system_defaults,
        action_memory_suggestion=None,
    ):
        payload = {}

        # Source 1: Resolved entities -> inject IDs + display names
        if "customer" in resolved:
            r = resolved["customer"]
            payload["customer_id"] = r.entity_id
            payload["customer_name"] = r.entity_name
        if "vendor" in resolved:
            r = resolved["vendor"]
            payload["vendor_id"] = r.entity_id
            payload["vendor_name"] = r.entity_name
        if "item" in resolved:
            r = resolved["item"]
            payload["item_id"] = r.entity_id
            payload["item_name"] = r.entity_name
        if "bank_account" in resolved:
            r = resolved["bank_account"]
            # Do NOT populate bank_account_id when ambiguous — user must pick via clarification.
            # Also STRIP any Stage-2-hallucinated bank_account_id / paid_through_id so the
            # orchestrator's pills shortcut can fire instead of proceeding with a guess.
            if len(r.candidates) <= 1:
                payload["bank_account_id"] = r.entity_id
                payload["bank_account_name"] = r.entity_name
            else:
                payload.pop("bank_account_id", None)
                payload.pop("bank_account_name", None)
                payload.pop("paid_through_id", None)
                payload.pop("paid_through_name", None)
        if "warehouse" in resolved:
            r = resolved["warehouse"]
            payload["warehouse_id"] = r.entity_id
            payload["warehouse_name"] = r.entity_name
        if "invoice" in resolved:
            r = resolved["invoice"]
            payload["invoice_id"] = r.entity_id
            payload["invoice_number"] = r.entity_name
            # Void/update/post sales_invoice|sales_order use registry field `id`.
            if (
                intent
                in (
                    "void_sales_invoice",
                    "update_sales_invoice",
                    "post_sales_invoice",  # FIX_POST_DRAFT 2026-06-20
                    "void_sales_order",
                    "update_sales_order",
                )
                and r.entity_id
            ):
                payload.setdefault("id", r.entity_id)
        if "bill" in resolved:
            r = resolved["bill"]
            payload["bill_id"] = r.entity_id
            payload["bill_number"] = r.entity_name
            # Void/update/post/delete bill use registry field `id`.
            if (
                intent
                in (
                    "void_bill",
                    "update_bill",
                    "post_bill",
                    "delete_bill",
                )
                and r.entity_id
            ):
                payload.setdefault("id", r.entity_id)
        if "account" in resolved:
            r = resolved["account"]
            payload["account_id"] = r.entity_id
            payload["account_name"] = r.entity_name

        # Intent-specific: map resolved names to registry field names
        if intent == "create_customer" and "customer" in resolved:
            payload.setdefault("name", resolved["customer"].entity_name)
        elif intent == "create_customer" and entities.get("customer_name"):
            payload.setdefault("name", entities["customer_name"])
        if intent == "create_vendor" and "vendor" in resolved:
            payload.setdefault("name", resolved["vendor"].entity_name)
        elif intent == "create_vendor" and entities.get("vendor_name"):
            payload.setdefault("name", entities["vendor_name"])
        if intent == "create_item" and entities.get("item_name"):
            payload.setdefault("name", entities["item_name"])
        if intent == "create_warehouse" and "warehouse" in resolved:
            payload.setdefault("name", resolved["warehouse"].entity_name)
        elif intent == "create_warehouse" and entities.get("warehouse_name"):
            payload.setdefault("name", entities["warehouse_name"])
        if intent == "create_bank_account" and entities.get("bank_name"):
            payload.setdefault("account_name", entities["bank_name"])

        # Source 1: Direct entity values (non-relational)
        direct_fields = [
            "amount",
            "quantity",
            "unit_price",
            "description",
            "date",
            "phone",
            "email",
            "address",
            "reason",
            "name",
            "account_type",
            "payment_method",
            "item_type",
            "base_unit",
        ]
        for field_name in direct_fields:
            if entities.get(field_name) is not None:
                payload[field_name] = entities[field_name]

        # 4C: deterministic payment gate — map `amount` -> `total_amount`
        # for payment intents whose registry FieldSpec is named `total_amount`.
        # Without this, validate_payload misses the required field and the
        # pipeline emits a TEXT clarification instead of DIRECT_ACTION_PREVIEW.
        if intent in ("create_receive_payment", "create_bill_payment"):
            if payload.get("amount") is not None and not payload.get("total_amount"):
                payload["total_amount"] = payload["amount"]

        # Registry-aware field injection (Stage 2 extracts exact registry names)
        from .direct_action_registry import get_direct_action

        _config = get_direct_action(intent)
        if _config:
            _registry_names = {f.name for f in _config.fields}
            for key, value in entities.items():
                # Iron Law 1 hardening: never trust LLM-extracted ID fields.
                # Stage-2 LLM (Gemini Flash Lite) can hallucinate UUID-shaped
                # values for *_id fields that by chance match real DB rows,
                # silently routing transactions to wrong entity. IDs MUST
                # come from the resolver path above (sources 1 / 2 / 2.5).
                # Ticket: 2026-05-07-stage2-llm-uuid-hallucination-audit.
                if is_untrusted_id_field(key):
                    if value:
                        logger.warning(
                            "[INVARIANT_GUARD] Stripped LLM-extracted ID field %r from payload (intent=%s); resolver is single source of truth",
                            key,
                            intent,
                        )
                    continue
                if key in _registry_names and value is not None and key not in payload:
                    payload[key] = value

        # Source 2: Memory state (fill gaps only)
        # FIX_ITEM_VENDOR_LEAK — gate counterparty (vendor/customer/invoice/bill)
        # memory injection. A prior vendor/customer interaction leaves
        # active_vendor_id / active_customer_id in chat_session_state. For
        # master-data create/update intents (item, vendor, customer, warehouse,
        # account) the counterparty has NO place in the payload — a vendor must
        # never enter an item payload, a customer must never enter a vendor
        # payload, etc. Injecting it leaked a stale "Terdeteksi dari vendor 'X'"
        # onto create_item cards. Skip counterparty memory injection for these.
        _MASTER_DATA_INTENTS = {
            "create_item",
            "update_item",
            "create_vendor",
            "update_vendor",
            "create_customer",
            "update_customer",
            "create_warehouse",
            "update_warehouse",
            "create_account",
            "update_account",
        }
        _skip_counterparty_memory = intent in _MASTER_DATA_INTENTS
        if memory_state and _skip_counterparty_memory:
            logger.debug(
                "[FIX_ITEM_VENDOR_LEAK] skipping vendor/customer/invoice/bill "
                "memory injection for master-data intent=%s",
                intent,
            )
        if memory_state and not _skip_counterparty_memory:
            if "customer_id" not in payload and memory_state.get("active_customer_id"):
                payload["customer_id"] = memory_state["active_customer_id"]
                payload.setdefault(
                    "customer_name", memory_state.get("active_customer_name", "")
                )
            if "vendor_id" not in payload and memory_state.get("active_vendor_id"):
                payload["vendor_id"] = memory_state["active_vendor_id"]
                payload.setdefault(
                    "vendor_name", memory_state.get("active_vendor_name", "")
                )
            if "invoice_id" not in payload and memory_state.get("active_invoice_id"):
                payload["invoice_id"] = memory_state["active_invoice_id"]
                payload.setdefault(
                    "invoice_number", memory_state.get("active_invoice_number", "")
                )
                if intent in (
                    "void_sales_invoice",
                    "update_sales_invoice",
                    "post_sales_invoice",  # FIX_POST_DRAFT 2026-06-20
                    "void_sales_order",
                    "update_sales_order",
                ):
                    payload.setdefault("id", memory_state["active_invoice_id"])
            if "bill_id" not in payload and memory_state.get("active_bill_id"):
                payload["bill_id"] = memory_state["active_bill_id"]
                payload.setdefault(
                    "bill_number", memory_state.get("active_bill_number", "")
                )
                if intent in (
                    "void_bill",
                    "update_bill",
                    "post_bill",
                    "delete_bill",
                ):
                    payload.setdefault("id", memory_state["active_bill_id"])

        # Source 2.5: Action Memory pattern (fill items/tax from learned patterns)
        if action_memory_suggestion and action_memory_suggestion.get("pattern"):
            pattern = action_memory_suggestion["pattern"]
            if "items" not in payload and pattern.get("items"):
                payload["items"] = [
                    {
                        "item_id": pi.get("item_id", ""),
                        "description": pi.get("name", ""),
                        "quantity": pi.get("last_qty", 1),
                        "unit_price": pi.get("last_price", 0),
                    }
                    for pi in pattern["items"]
                ]
            if "tax_rate" not in payload and pattern.get("tax_rate") is not None:
                payload["tax_rate"] = pattern["tax_rate"]
            if "bank_account_id" not in payload and pattern.get("bank_account_id"):
                payload["bank_account_id"] = pattern["bank_account_id"]
                payload.setdefault(
                    "bank_account_name", pattern.get("bank_account_name", "")
                )
            if "account_id" not in payload and pattern.get("account_id"):
                payload["account_id"] = pattern["account_id"]
                payload.setdefault("account_name", pattern.get("account_name", ""))

        # Source 3: System defaults
        for key, value in system_defaults.items():
            payload.setdefault(key, value)

        # ── Intent-specific payload construction (Tahap 2b) ──────────

        # receive_payment: needs allocations array from resolved invoice
        if intent == "create_receive_payment" and "invoice" in resolved:
            inv = resolved["invoice"]
            amount = entities.get("amount")
            if amount and "allocations" not in payload:
                payload["allocations"] = [
                    {
                        "invoice_id": inv.entity_id,
                        "invoice_number": inv.entity_name,
                        "amount": amount,
                    }
                ]

        # bill_payment: needs allocations array from resolved bill
        elif intent == "create_bill_payment" and "bill" in resolved:
            bill = resolved["bill"]
            amount = entities.get("amount")
            if amount and "allocations" not in payload:
                payload["allocations"] = [
                    {
                        "bill_id": bill.entity_id,
                        "bill_number": bill.entity_name,
                        "amount": amount,
                    }
                ]

        # sales_invoice: needs items array from resolved item
        elif intent == "create_sales_invoice" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "description": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # sales_order: needs items array from resolved item (mirror sales_invoice)
        elif intent == "create_sales_order" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "description": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # quote: needs items array from resolved item (schema: description required, unit_price int)
        elif intent == "create_quote" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "description": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # bill (faktur pembelian): needs items array, field names differ
        elif intent == "create_bill" and "item" in resolved:
            item = resolved["item"]
            # FIX_DOGFOOD_BILL_DUEDATE 2026-06-09: Stage-2 LLM sometimes returns
            # items as an empty STRING ("") for the json-typed FieldSpec. The old
            # guard `"items" not in payload` treated "" as present -> scalar-build
            # skipped -> validate_payload sees falsy items -> spurious "missing Item"
            # clarification (mis-narrated by LLM as a due_date question). Treat any
            # falsy items (empty string/list/None) as "needs build" so the bill
            # proposes directly, mirroring how the sales path stays populated.
            if not payload.get("items"):
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "item_name": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # expense: map bank_account_id to paid_through_id (only when unambiguous)
        elif intent == "create_expense":
            if "bank_account" in resolved and "paid_through_id" not in payload:
                _ba = resolved["bank_account"]
                if len(_ba.candidates) <= 1:
                    payload["paid_through_id"] = _ba.entity_id
                    payload["paid_through_name"] = _ba.entity_name

        # Intent-specific date mapping
        if "date" not in payload:
            payload["date"] = system_defaults.get("date", "")
        if intent.startswith("create_") and intent not in (
            "create_customer",
            "create_vendor",
            "create_warehouse",
            "create_bank_account",
            "create_item",
        ):
            date_val = payload.pop("date", system_defaults.get("date", ""))
            if date_val:
                if "payment" in intent:
                    payload.setdefault("payment_date", date_val)
                elif "invoice" in intent or "bill" in intent:
                    payload.setdefault("invoice_date", date_val)
                elif "expense" in intent:
                    payload.setdefault("expense_date", date_val)
                elif "journal" in intent:
                    payload.setdefault("entry_date", date_val)

        return payload

    # Individual Entity Resolvers

    async def _resolve_customer(self, name_fragment: str) -> Optional[ResolvedEntity]:
        """customers.id = UUID (terverifikasi [SQL] 2026-08-09); kolom nama = `nama` (Bahasa!).
        Tipe customer_id tak seragam: uuid di customers/sales_invoices/receive_payments,
        varchar di credit_notes/customer_deposits. Jangan menyamaratakan.
        FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: thread actual pg_trgm sim; no blanket 1.0."""
        try:
            rows = await self.db.fetch(
                """SELECT id, nama, telepon, email
                   FROM customers
                   WHERE tenant_id = $1 AND is_active = true
                     AND nama ILIKE $2
                   ORDER BY total_transaksi DESC NULLS LAST
                   LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            match_kind = "substring" if rows else None
            if not rows:
                # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: raised 0.15 -> 0.5
                rows = await self.db.fetch(
                    """SELECT id, nama, telepon, email,
                              similarity(nama, $2) AS sim
                       FROM customers
                       WHERE tenant_id = $1 AND is_active = true
                         AND similarity(nama, $2) > 0.5
                       ORDER BY sim DESC LIMIT 5""",
                    self.tenant_id,
                    name_fragment,
                )
                if rows:
                    match_kind = "fuzzy"
            if not rows:
                return ResolvedEntity(
                    entity_type="customer",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["nama"]} for r in rows]
            best = candidates[0]
            # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: decouple confidence from candidate count
            if match_kind == "fuzzy":
                try:
                    confidence = float(rows[0]["sim"])
                except Exception:
                    confidence = 0.5
            else:
                confidence = 0.9  # substring ILIKE match
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="customer",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Customer lookup failed: %s", e)
            return None

    async def _resolve_vendor(self, name_fragment: str) -> Optional[ResolvedEntity]:
        """FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: thread actual pg_trgm sim; no blanket 1.0."""
        try:
            rows = await self.db.fetch(
                """SELECT id, name FROM vendors
                   WHERE tenant_id = $1 AND is_active = true AND name ILIKE $2
                   ORDER BY name LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            match_kind = "substring" if rows else None
            if not rows:
                # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: raised 0.15 -> 0.5
                rows = await self.db.fetch(
                    """SELECT id, name,
                              similarity(name, $2) AS sim
                       FROM vendors
                       WHERE tenant_id = $1 AND is_active = true
                         AND similarity(name, $2) > 0.5
                       ORDER BY sim DESC LIMIT 5""",
                    self.tenant_id,
                    name_fragment,
                )
                if rows:
                    match_kind = "fuzzy"
            if not rows:
                return ResolvedEntity(
                    entity_type="vendor",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["name"]} for r in rows]
            best = candidates[0]
            # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: decouple confidence from candidate count
            if match_kind == "fuzzy":
                try:
                    confidence = float(rows[0]["sim"])
                except Exception:
                    confidence = 0.5
            else:
                confidence = 0.9
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="vendor",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Vendor lookup failed: %s", e)
            return None

    async def _resolve_item(
        self, name_fragment: str, user_text: str = ""
    ) -> Optional[ResolvedEntity]:
        """products.nama_produk (Bahasa!) — with fuzzy fallback for typos."""
        # T144 FASE 2 — JEJAK WAJIB. Fungsi ini MENCARI barang yang SUDAH ADA.
        # Pada jalur bulk create_item ia tidak boleh terpanggil: ambang fuzzy
        # 0.5 meloloskan similarity("(2XL)","(3XL)")=0.714, dan pemanggilnya
        # di tool_executor memakai `exact or results[0]` — tebakan buta yang
        # akan MELEBUR lima ukuran jadi satu. Pagarnya ada di resolve_and_complete
        # (`not intent.startswith("create_item")`), tapi pagar tanpa jejak tak
        # bisa diuji: baris ini yang membuat "nol pemanggilan" jadi PENGUKURAN,
        # bukan pengandaian.
        logger.warning(
            "[RESOLVE_ITEM] _resolve_item dipanggil, fragment=%r", name_fragment
        )
        try:
            search_term = name_fragment.strip()

            # ── Step 0 (T196): EXACT TERNORMALISASI, sebelum apa pun ────────
            # KENAPA harus di sini dan bukan cukup di loop exact-match bawah:
            # loop itu bekerja ATAS `candidates`, sedangkan baris yang benar
            # sudah TERSINGKIR lebih dulu di lapisan ILIKE. Terukur pada
            # fragment 'Kaos 20s + Sablon Plastisol (Size 3XL)': token '(Size'
            # BERTAHAN (cocok 4 baris berkurung) sementara '3XL)' dibuang,
            # sehingga satu-satunya baris yang benar -- 'Kaos 20s + Sablon
            # Plastisol size 3XL', TANPA kurung -- disaring keluar sebelum
            # `candidates` terbentuk, dan sistem mengikat DIAM-DIAM ke (Size
            # 2XL) dengan confidence 0.9. Jadi normalisasi harus dapat
            # kesempatan SEBELUM penyaringan itu terjadi.
            #
            # Ini BUKAN pelebaran pencarian: kesamaan PENUH, bukan substring,
            # bukan fuzzy. Risikonya hanya dua nama master yang MELEBUR setelah
            # normalisasi -- terukur NOL di seluruh milkydb. ⚠️ populasinya
            # kecil (65 produk, 2 tenant), jadi angka itu BUKAN jaminan
            # permanen; pagarnya adalah LIMIT 2 di bawah, bukan angka nol itu.
            #
            # LIMIT 2 disengaja: TEPAT 1 -> putuskan; 0 atau >= 2 -> JANGAN
            # putuskan apa pun, lanjut ke Step 1 seperti biasa. Dengan begitu
            # Step 0 hanya bisa MENAMBAH pengikatan benar, tak pernah mengurangi.
            _s0_norm = _norm_cocok(name_fragment)
            if _s0_norm:
                _s0_rows = await self.db.fetch(
                    _SQL_EXACT_TERNORMALISASI, self.tenant_id, _s0_norm
                )
                if len(_s0_rows) == 1:
                    # T89: logger modul ini TIDAK punya handler .info -> .warning.
                    logger.warning(
                        "[RESOLVE][T196] Step 0 exact ternormalisasi: %r -> %r "
                        "(1 baris, diikat confidence 1.0)",
                        name_fragment,
                        _s0_rows[0]["nama_produk"],
                    )
                    return ResolvedEntity(
                        entity_type="item",
                        entity_id=str(_s0_rows[0]["id"]),
                        entity_name=_s0_rows[0]["nama_produk"],
                        confidence=1.0,
                        candidates=[
                            {
                                "id": str(_s0_rows[0]["id"]),
                                "name": _s0_rows[0]["nama_produk"],
                            }
                        ],
                        low_trust=False,
                    )
                if len(_s0_rows) >= 2:
                    logger.warning(
                        "[RESOLVE][T196] Step 0 TIDAK memutuskan: %r cocok >= 2 "
                        "baris ternormalisasi -- lanjut ke Step 1",
                        name_fragment,
                    )

            # Step 1: Exact ILIKE match (full name)
            rows = await self.db.fetch(
                """SELECT id, nama_produk, sales_price_amount, purchase_price_amount, item_type
                   FROM products
                   WHERE tenant_id = $1 AND status = 'active' AND deleted_at IS NULL
                     AND (nama_produk ILIKE $2 OR item_code ILIKE $2 OR sku ILIKE $2)
                   ORDER BY nama_produk LIMIT 5""",
                self.tenant_id,
                f"%{search_term}%",
            )

            # Step 2: PENYEMPITAN BERTAHAP — semua token user di-AND-kan.
            #
            # J0 2026-08-12: dulu langkah ini memakai KATA PERTAMA saja
            # (`name_fragment.split()[0]`), dan itu membuang justru token yang
            # membedakan. "kaos hitam 30s" -> Step 1 nol hasil (master bernama
            # "Kaos Hitam Gramasi 30s" tak memuat substring itu) -> fallback
            # mencari "%kaos%" -> DUA kandidat -> pil disambiguasi muncul
            # padahal user sudah menyebut variannya dengan tepat.
            #
            # Akibatnya sistematis dan terbalik: SEMAKIN SPESIFIK user mengetik,
            # semakin panjang frasanya, semakin pasti Step 1 gagal, dan semakin
            # jauh fallback melempar ke kata yang paling umum. Ketelitian user
            # dihukum. Terjadi pada SETIAP transaksi kaos (dogfood 2026-08-12).
            #
            # ARAH AND: token USER harus semuanya ada di nama master, BUKAN
            # sebaliknya. "kaos hitam 30s" cocok "Kaos Hitam Gramasi 30s" meski
            # "gramasi" tak diketik. Arah sebaliknya akan menuntut owner
            # mengetik nama lengkap setiap kali — menukar satu gangguan dengan
            # yang lebih parah.
            #
            # NOL daftar stop-word. Ekstraksi sudah memisahkan qty/satuan/pihak
            # ke field sendiri (item_name = "kaos hitam 30s", quantity = 50,
            # base_unit = "pcs"), jadi yang masuk ke sini memang hanya nama
            # barang. Daftar stop-word akan jadi tebakan yang tak dibutuhkan,
            # dan selalu salah untuk sebagian tenant — bayangkan barang bernama
            # "Kaos Untuk Anak". Penyaringnya panjang token (>= 2 huruf):
            # aturan yang bisa dijelaskan tanpa mengenal kosakata bisnis siapa
            # pun.
            #
            # Step 1 (ILIKE frasa penuh) SENGAJA DIPERTAHANKAN di atas sebagai
            # jalur cepat: bila frasa penuh cocok, tak perlu memecah token.
            _low_trust = False  # G1 (T97)
            _tokens = [t for t in name_fragment.split() if len(t) >= 2]
            if not rows and len(_tokens) > 1:

                async def _cari_and(tokens):
                    _kondisi = " AND ".join(
                        f"(nama_produk ILIKE ${i + 2} OR item_code ILIKE ${i + 2} "
                        f"OR sku ILIKE ${i + 2})"
                        for i in range(len(tokens))
                    )
                    return await self.db.fetch(
                        f"""SELECT id, nama_produk, sales_price_amount,
                                   purchase_price_amount, item_type
                            FROM products
                            WHERE tenant_id = $1 AND status = 'active' AND deleted_at IS NULL
                              AND {_kondisi}
                            ORDER BY nama_produk LIMIT 5""",
                        self.tenant_id,
                        *[f"%{t}%" for t in tokens],
                    )

                # T137 2026-08-26: pencarian menurut CAKUPAN TOKEN, diangkat
                # dari cabang T113 di bawah supaya DUA cabang memakai SATU
                # mekanisme yang sama. Tidak ada perubahan perilaku pada T113:
                # pemanggilnya melewatkan argumen yang persis sama.
                async def _cari_cakupan(tokens, min_cakupan):
                    _ekspr = " + ".join(
                        f"(CASE WHEN (nama_produk ILIKE ${i + 2} "
                        f"OR item_code ILIKE ${i + 2} "
                        f"OR sku ILIKE ${i + 2}) THEN 1 ELSE 0 END)"
                        for i in range(len(tokens))
                    )
                    return await self.db.fetch(
                        f"""SELECT * FROM (
                                SELECT id, nama_produk, sales_price_amount,
                                       purchase_price_amount, item_type,
                                       ({_ekspr}) AS cakupan
                                FROM products
                                WHERE tenant_id = $1 AND status = 'active' AND deleted_at IS NULL
                            ) AS c
                            WHERE cakupan >= {min_cakupan}
                            ORDER BY cakupan DESC, nama_produk
                            LIMIT 5""",
                        self.tenant_id,
                        *[f"%{t}%" for t in tokens],
                    )

                rows = await _cari_and(_tokens)
                search_term = " ".join(_tokens)

                # Longgarkan SATU tingkat: buang token yang sendirian pun nol
                # hasil (umumnya salah ketik), lalu AND ulang sisanya. Ini yang
                # menangani "kaos hitm 30s": "hitm" dibuang, AND(kaos, 30s)
                # menyisakan tepat satu master. Token yang dibuang TIDAK
                # menghapus batasan yang berarti — ia memang tak cocok apa pun.
                if not rows:
                    _hidup = []
                    for _t in _tokens:
                        _cek = await self.db.fetch(
                            """SELECT 1 FROM products
                               WHERE tenant_id = $1 AND status = 'active' AND deleted_at IS NULL
                                 AND (nama_produk ILIKE $2 OR item_code ILIKE $2
                                      OR sku ILIKE $2) LIMIT 1""",
                            self.tenant_id,
                            f"%{_t}%",
                        )
                        if _cek:
                            _hidup.append(_t)
                    # T113 2026-08-24: PERHATIAN — dulu blok ini dijaga oleh
                    # `if _hidup and len(_hidup) < len(_tokens)`. Klausa
                    # `len(_hidup) < len(_tokens)` SENGAJA DICABUT dari syarat
                    # MASUK; ia TIDAK hilang karena kelalaian. Alasannya:
                    #
                    # Kita berada DI DALAM `if not rows` (Step 2 AND penuh nol).
                    # Posisi kode itu SUDAH membuktikan "tak ada satu baris pun
                    # yang memuat SEMUA token". Menambahkan `len(_hidup) <
                    # len(_tokens)` di depan pintu berarti bertanya hal LAIN:
                    # "adakah token yang mati di mana pun di master?" — pertanyaan
                    # yang tidak diminta, dan yang jawabannya TIDAK menentukan
                    # apakah user perlu dibantu memilih.
                    #
                    # Kasus yang terjatuh lewat celah itu: "kaos hitam 30s" di
                    # tenant yang punya "Kaos Hitam 24s" dan "Kaos Biru 30s".
                    # Ketiga token HIDUP (kaos, hitam, 30s semuanya ada di suatu
                    # baris), tapi tak satu baris memuat ketiganya. Gerbang lama
                    # -> False -> blok dilewati -> Step 3 fuzzy melawan frasa
                    # penuh: Kaos Hitam 24s = 0.5789 LOLOS, Kaos Biru 30s = 0.45
                    # DITOLAK. Padahal keduanya cocok 2 dari 3 token; yang
                    # memisahkan hanya artefak trigram 0,13. User dapat pil SATU
                    # opsi tanpa jalan keluar.
                    #
                    # Sekarang klausa itu turun derajat jadi syarat LOKAL: ia
                    # hanya memilih sub-langkah mana yang dipakai di bawah.
                    if _hidup:
                        if len(_hidup) < len(_tokens):
                            # G1 (T97) 2026-08-23: pelonggaran di atas hanya SAH untuk
                            # SALAH KETIK. Asumsi commit 6d9b445c ("token yang dibuang
                            # tidak menghapus batasan yang berarti") benar untuk "hitm",
                            # tapi SALAH untuk token ASING: "hitam" nol hasil bukan
                            # karena salah ketik, melainkan karena barang hitam memang
                            # TIDAK ADA di master. Membuangnya menghapus justru batasan
                            # yang paling berarti, lalu AND sisanya ("kaos") menyisakan
                            # tepat satu baris -> confidence 0.9 -> diterima DIAM-DIAM.
                            #
                            # Pemisahnya terukur (pg_trgm, token vs token):
                            #   "hitm"  <-> "hitam"                = 0.375  (salah ketik)
                            #   "hitam" <-> token master mana pun  = 0.000  (asing)
                            # Ambang 0.25 duduk di celah itu: 0.125 di bawah sisi salah
                            # ketik, 0.25 penuh di atas sisi asing. Sengaja diambil di
                            # ujung BAWAH celah -- sisi asing terukur nol, jadi ambang
                            # rendah tak melemahkan penolakan sama sekali, sementara
                            # ambang rendah memberi ruang paling lega bagi salah ketik
                            # yang HARI INI sudah bekerja benar (jangan tambah friksi).
                            #
                            # Bandingkan TOKEN lawan TOKEN, bukan lawan nama penuh:
                            # similarity("kaos hitam", "Kaos Biru 30s") = 0.25 -- cukup
                            # tinggi untuk menipu ambang ini. Token-lawan-token = 0.000.
                            _dibuang = [t for t in _tokens if t not in _hidup]
                            _asing = []
                            for _t in _dibuang:
                                _dekat = await self.db.fetch(
                                    _SQL_TETANGGA_DEKAT,
                                    self.tenant_id,
                                    _t.lower(),
                                    _AMBANG_TOKEN_ASING,
                                )
                                if not _dekat:
                                    _asing.append(_t)
                            rows = await _cari_and(_hidup)
                            search_term = " ".join(_hidup)
                            # T137 2026-08-26 — GERBANG CAKUPAN TOKEN.
                            #
                            # Sampai di sini berarti: Step 2 AND penuh nol DAN
                            # Step 2b AND(_hidup) juga nol. Dulu jalur ini
                            # berakhir dengan rows=[] -> _low_trust=bool([])=False
                            # -> Step 3 fuzzy (0.42/0.33 < 0.5) -> confidence 0.0
                            # -> `missing.append("item")` TANPA satu pun
                            # clarification -> kartu lahir DIAM-DIAM sebagai teks
                            # bebas: description = kalimat user, item_id null,
                            # unit_price absen -> harga 0. Owner tidak pernah
                            # ditanya apa pun.
                            #
                            # Pembedanya SATU ANGKA: `len(_hidup)` — berapa token
                            # user (>= 2 huruf) yang cocok baris master aktif mana
                            # pun di tenant ini.
                            #   len(_hidup) == 0 -> user memang bicara di luar
                            #     master (jasa sablon/bordir/konsultasi). Blok ini
                            #     tak pernah dimasuki (`if _hidup:` di atas False),
                            #     teks bebas lahir LANGSUNG tanpa pertanyaan. PAGAR
                            #     ITU TIDAK BOLEH MATI.
                            #   len(_hidup) == 1 -> `_cari_and` satu token sudah
                            #     mengembalikan baris (mis. "kaos ungu" -> 2 baris)
                            #     -> jalur T97 lama, tak diubah.
                            #   len(_hidup) >= 2 TAPI nol baris memuat semuanya ->
                            #     user jelas memaksudkan SESUATU DI MASTER dengan
                            #     kombinasi yang tidak ada. Itu pertanyaan, bukan
                            #     barang baru.
                            #
                            # Terukur pada 14 deskripsi unik / 2 tenant: 12/12
                            # teks-bebas SAH punya len(_hidup)=0; 2/2 kasus salah
                            # punya len(_hidup)>=2. Nol tumpang tindih.
                            #
                            # Mekanismenya SENGAJA sama persis dengan cabang T113
                            # di bawah (`_cari_cakupan` + `_low_trust = True`):
                            # dua cabang, satu mesin, satu jalur pil. Cakupan
                            # dihitung terhadap _tokens PENUH (bukan _hidup) agar
                            # peringkat kandidat mencerminkan yang user ketik.
                            if not rows and len(_hidup) >= 2:
                                rows = await _cari_cakupan(_tokens, 2)
                                search_term = " ".join(_tokens)
                                _low_trust = True
                                # T89: WAJIB .warning agar baris ini terbit.
                                logger.warning(
                                    "[RESOLVE][T137] Step 2b AND(%s) nol baris "
                                    "pada %r; n_hidup=%d >= 2 -> gerbang cakupan "
                                    "token: %d kandidat (min cakupan 2, token "
                                    "asing=%s) -- pil + opsi keluar, kartu TIDAK "
                                    "dilahirkan diam-diam",
                                    _hidup,
                                    name_fragment,
                                    len(_hidup),
                                    len(rows),
                                    _asing,
                                )
                            elif _asing:
                                # T89: logger modul ini tak punya handler untuk .info --
                                # WAJIB .warning agar baris ini benar-benar terbit.
                                #
                                # M3c 2026-08-26: kalimat lama berbunyi "kandidat
                                # ditawarkan sebagai pil, bukan diikat" TANPA SYARAT,
                                # padahal `_low_trust = bool(rows)` di bawahnya bisa
                                # False saat rows=[] — nol kandidat, nol pil, kartu
                                # lahir diam-diam. Jejak owner 2026-08-26T01:59:59Z
                                # memuat baris itu dan membacanya sebagai bukti pil
                                # muncul. Log yang berbohong lebih berbahaya daripada
                                # nol log. Sekarang jumlah baris DIUKUR dan akibatnya
                                # DINYATAKAN apa adanya.
                                _low_trust = bool(rows)
                                logger.warning(
                                    "[RESOLVE][T97] token asing %s pada %r -- hasil "
                                    "pelonggaran Step 2b TIDAK DIPERCAYA (tak ada "
                                    "tetangga dekat >= %s di master tenant); "
                                    "AND(%s) -> %d baris; low_trust=%s -> %s",
                                    _asing,
                                    name_fragment,
                                    _AMBANG_TOKEN_ASING,
                                    _hidup,
                                    len(rows),
                                    _low_trust,
                                    (
                                        "kandidat ditawarkan sebagai pil, bukan diikat"
                                        if _low_trust
                                        else "NOL kandidat -- lanjut ke Step 3 fuzzy"
                                    ),
                                )
                        else:
                            # T113: SEMUA token hidup, tapi nol baris memuat
                            # semuanya. Tidak ada token yang bisa "dibuang"
                            # (pelonggaran salah-ketik di atas tak berlaku), dan
                            # menyerahkannya ke Step 3 fuzzy berarti memvonis
                            # lewat trigram nama-penuh — ukuran yang tidak
                            # mewakili apa yang user ketik.
                            #
                            # Yang benar: kumpulkan kandidat menurut CAKUPAN
                            # TOKEN tertinggi (berapa banyak token user yang
                            # muncul di baris itu), lalu tawarkan sebagai pil.
                            # Cakupan minimum 2 token bila user mengetik >= 2
                            # token — satu token generik ("kaos") bukan sinyal,
                            # ia akan menarik seluruh katalog.
                            #
                            # low_trust = True SELALU di cabang ini: menurut
                            # konstruksi TIDAK ADA kandidat yang memuat semua
                            # token, jadi kandidat terbaik pun mungkin salah
                            # semua -> user WAJIB diberi opsi keluar.
                            #
                            # LIMIT 5 mengikuti seluruh jalur resolusi lain di
                            # berkas ini (Step 1, _cari_and, Step 3, _resolve_*).
                            _min_cakupan = 2 if len(_tokens) >= 2 else 1
                            rows = await _cari_cakupan(_tokens, _min_cakupan)
                            if rows:
                                # T89: logger modul ini tak punya handler untuk
                                # .info -- WAJIB .warning agar baris ini terbit.
                                logger.warning(
                                    "[RESOLVE][T113] nol baris memuat SEMUA token "
                                    "%s pada %r; %d kandidat dipilih menurut "
                                    "cakupan token (min %d) -- ditawarkan sebagai "
                                    "pil dengan opsi keluar, tidak diikat",
                                    _tokens,
                                    name_fragment,
                                    len(rows),
                                    _min_cakupan,
                                )
                                search_term = " ".join(_tokens)
                                _low_trust = True

            # Step 3: Fuzzy match via pg_trgm (handles typos like "obyat" -> "obat")
            # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: substring is tracked as kind "substring"
            match_kind = "substring" if rows else None
            if not rows:
                # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: raised 0.15 -> 0.5
                rows = await self.db.fetch(
                    """SELECT id, nama_produk, sales_price_amount, purchase_price_amount, item_type,
                              similarity(nama_produk, $2) AS sim
                       FROM products
                       WHERE tenant_id = $1 AND status = 'active' AND deleted_at IS NULL
                         AND similarity(nama_produk, $2) > 0.5
                       ORDER BY sim DESC LIMIT 5""",
                    self.tenant_id,
                    name_fragment.strip(),
                )
                if rows:
                    match_kind = "fuzzy"
            if not rows:
                return ResolvedEntity(
                    entity_type="item",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            # T196 (C) — SARINGAN TEKS MENTAH. Nama MASTER dicari DI DALAM teks
            # user (bukan sebaliknya): itu yang membuat "Bunaken Oasis",
            # "tanggal", "19 pcs" tak mengganggu. Syarat `== 1` MUTLAK —
            # terukur: teks 5 baris memuat 5 nama master, saringan mengembalikan
            # 5, bukan 1. Saringan hanya boleh MENAMBAH pengikatan benar,
            # tak pernah mengurangi.
            if len(rows) > 1 and user_text:
                _ltxt = _norm_cocok(user_text)
                _tersebut = [
                    r for r in rows if _norm_cocok(r["nama_produk"]) in _ltxt
                ]
                if len(_tersebut) == 1:
                    # T89: logger modul ini TIDAK punya handler .info -> .warning.
                    logger.warning(
                        "[RESOLVE][T196] %d kandidat menyempit jadi 1 lewat teks "
                        "mentah: %r disebut utuh oleh user",
                        len(rows),
                        _tersebut[0]["nama_produk"],
                    )
                    rows = _tersebut
                    _low_trust = False
                else:
                    logger.warning(
                        "[RESOLVE][T196] saringan teks mentah TIDAK menyempitkan: "
                        "%d kandidat, %d disebut utuh -- rows dipertahankan apa adanya",
                        len(rows),
                        len(_tersebut),
                    )
            candidates = [{"id": str(r["id"]), "name": r["nama_produk"]} for r in rows]
            best = candidates[0]
            # FIX_AQUA_FUZZY_TIGHTEN 2026-05-19: decouple confidence from candidate count
            if match_kind == "fuzzy":
                try:
                    confidence = float(rows[0]["sim"])
                except Exception:
                    confidence = 0.5
            elif _low_trust:
                # G1 (T97): JANGAN 0.9. Pita 0.5 <= conf < 0.85 adalah pita
                # "konfirmasi dulu" yang SUDAH ADA (entity_resolver:181-192 dan
                # orchestrator._ep_is_ambiguous): satu kandidat pun tetap
                # dimunculkan sebagai pil, bukan diikat diam-diam.
                # Confidence 0.9 untuk hasil Step 1 / Step 2 biasa TIDAK diubah.
                confidence = 0.6
            else:
                confidence = 0.9
            for c in candidates:
                if _norm_cocok(c["name"]) == _norm_cocok(name_fragment):
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="item",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
                low_trust=_low_trust,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Item lookup failed: %s", e)
            return None

    async def _resolve_bank_account(
        self, name_fragment: str
    ) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, account_name, bank_name, coa_id
                   FROM bank_accounts
                   WHERE tenant_id = $1 AND is_active = true
                     AND (account_name ILIKE $2 OR bank_name ILIKE $2)
                   ORDER BY account_name LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="bank_account",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["account_name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            # Exact match boost: if one candidate matches exactly, pick it
            _matched_exact = False
            for i, r in enumerate(rows):
                if r["account_name"].lower().strip() == name_fragment.lower().strip():
                    best = candidates[i]
                    confidence = 1.0
                    candidates = [best]  # collapse to single match
                    _matched_exact = True
                    break
            # Bank-name ambiguity: when user typed a short identifier (e.g. "BCA")
            # matching multiple accounts, DO NOT silently collapse. Preserve all
            # candidates so orchestrator emits a CLARIFICATION with pills.
            # Collapsing destroys user intent (wrong account picked).
            if not _matched_exact and len(candidates) > 1:
                logger.warning(
                    "[RESOLVE] Bank ambiguity preserved for clarification: fragment=%r matched %d accounts: %s",
                    name_fragment,
                    len(candidates),
                    [c["name"] for c in candidates],
                )
                confidence = 0.7  # force needs_clarification in resolve_and_complete
            return ResolvedEntity(
                entity_type="bank_account",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Bank account lookup failed: %s", e)
            return None

    async def _resolve_warehouse(self, name_fragment: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, name FROM warehouses
                   WHERE tenant_id = $1 AND name ILIKE $2
                   ORDER BY name LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="warehouse",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type="warehouse",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Warehouse lookup failed: %s", e)
            return None

    async def _resolve_invoice(self, invoice_number: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, invoice_number, customer_id, status
                   FROM sales_invoices
                   WHERE tenant_id = $1 AND invoice_number ILIKE $2
                   ORDER BY created_at DESC LIMIT 5""",
                self.tenant_id,
                f"%{invoice_number}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="invoice",
                    entity_id="",
                    entity_name=invoice_number,
                    confidence=0.0,
                )
            candidates = [
                {"id": str(r["id"]), "name": r["invoice_number"]} for r in rows
            ]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type="invoice",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Invoice lookup failed: %s", e)
            return None

    async def _resolve_bill(self, bill_number: str) -> Optional[ResolvedEntity]:
        """Column = invoice_number (legacy naming)."""
        try:
            rows = await self.db.fetch(
                """SELECT id, invoice_number, vendor_id, vendor_name, status
                   FROM bills
                   WHERE tenant_id = $1 AND invoice_number ILIKE $2
                   ORDER BY created_at DESC LIMIT 5""",
                self.tenant_id,
                f"%{bill_number}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="bill",
                    entity_id="",
                    entity_name=bill_number,
                    confidence=0.0,
                )
            candidates = [
                {"id": str(r["id"]), "name": r["invoice_number"]} for r in rows
            ]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type="bill",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Bill lookup failed: %s", e)
            return None

    async def _resolve_aksi_dokumen(self, aksi, entities: dict) -> ResolutionResult:
        """Resolusi untuk satu baris DOCUMENT_ACTIONS. Table-driven.

        Empat keadaan, tiga di antaranya BERHENTI dengan kalimat manusia:
          1. nomor tak disebut      -> tanya nomornya (JANGAN menebak)
          2. nomor tak ditemukan    -> sebut nomornya, minta dicek
          3. nomor ambigu           -> tampilkan kandidat, tanya yang mana
          4. status tak memenuhi    -> sebut dokumen HASILNYA kalau ada
        Baru sesudah keempatnya lewat, payload kartu dibangun.
        """
        hasil = ResolutionResult()
        nomor = (entities or {}).get(aksi.field_nomor) or (entities or {}).get("name")
        nomor = str(nomor).strip() if nomor else ""

        if not nomor:
            hasil.needs_clarification = True
            hasil.clarifications.append(
                f"{aksi.sebutan} mana yang mau {aksi.kata_kerja_pasif}"
                + (f" jadi {aksi.sebutan_tujuan}" if aksi.sebutan_tujuan else "")
                + "? Sebutkan nomornya, misalnya QUO-2608-0002."
            )
            return hasil

        ref = await self._resolve_by_number(
            nomor,
            table=aksi.tabel,
            number_column=aksi.kolom_nomor,
            entity_type=aksi.entity_type,
        )
        # Gerbang M1e: objek saja tidak cukup. Kontrak sumber kini
        # mengembalikan None saat gagal, dan syarat ganda di bawah menahan
        # entity_id kosong seandainya kontrak itu kelak berubah lagi.
        if ref is None or not ref.entity_id or ref.confidence <= 0:
            hasil.needs_clarification = True
            hasil.clarifications.append(
                f"{aksi.sebutan} {nomor} tidak ditemukan. Coba cek nomornya."
            )
            return hasil

        if len(ref.candidates) > 1:
            daftar = ", ".join(c["name"] for c in ref.candidates)
            hasil.needs_clarification = True
            hasil.clarifications.append(
                f"Ada {len(ref.candidates)} dokumen yang cocok dengan '{nomor}': "
                f"{daftar}. Yang mana?"
            )
            return hasil

        kolom = ["status"] + [k for k, _ in aksi.kolom_ringkas]
        if aksi.kolom_tujuan_id:
            kolom.append(aksi.kolom_tujuan_id)
        kolom_unik = list(dict.fromkeys(kolom))
        baris = await self.db.fetchrow(
            f'SELECT {", ".join(kolom_unik)} FROM {aksi.tabel} '
            f"WHERE id = $1 AND tenant_id = $2",
            __import__("uuid").UUID(ref.entity_id),
            self.tenant_id,
        )
        if baris is None:
            hasil.needs_clarification = True
            hasil.clarifications.append(
                f"{aksi.sebutan} {ref.entity_name} tidak ditemukan. "
                "Coba cek nomornya."
            )
            return hasil

        status = baris["status"]
        if status not in aksi.status_boleh:
            # GERBANG DEPAN. Endpoint tetap menjaga di belakang (400) — dua
            # sisi, sengaja. Yang di depan ada supaya user tak pernah melihat
            # kartu untuk aksi yang pasti ditolak; yang di belakang ada karena
            # status bisa berubah antara kartu dibuat dan dikonfirmasi.
            # Memasang yang depan lalu menganggap yang belakang tak perlu
            # adalah bentuk yang sudah tiga kali kita bayar.
            tujuan = ""
            if aksi.kolom_tujuan_id and baris.get(aksi.kolom_tujuan_id):
                try:
                    b2 = await self.db.fetchrow(
                        f"SELECT {aksi.kolom_nomor_tujuan} AS n "
                        f"FROM {aksi.tabel_tujuan} WHERE id = $1 AND tenant_id = $2",
                        baris[aksi.kolom_tujuan_id],
                        self.tenant_id,
                    )
                    if b2 and b2["n"]:
                        tujuan = f" jadi {aksi.sebutan_tujuan} {b2['n']}"
                except Exception as e:  # noqa: BLE001
                    logger.warning("[AKSI_DOK] lookup dokumen tujuan gagal: %s", e)
            # "jadi <tujuan>" hanya sah kalau aksinya PUNYA dokumen tujuan.
            # quote_send menghasilkan penawaran yang sama berstatus lain, jadi
            # tanpa syarat ini kalimatnya berakhir "belum bisa dikirim jadi ."
            _ke = f" jadi {aksi.sebutan_tujuan}" if aksi.sebutan_tujuan else ""
            # "sudah pernah dikonversi" hanya benar untuk aksi yang MEMANG
            # mengonversi. quote_send memakai kata kerja "dikirim", sehingga
            # cabang ini membuatnya berkata "sudah pernah dikirim" untuk
            # penawaran yang sebenarnya sudah DIKONVERSI — bot mengarang
            # riwayat, dan owner tak punya cara tahu itu keliru. Cabang ini
            # milik aksi yang punya dokumen tujuan; sisanya memakai kalimat
            # status biasa, yang menyebut keadaan sebenarnya.
            if status == "converted" and aksi.kolom_tujuan_id:
                pesan = (
                    f"{aksi.sebutan} {ref.entity_name} sudah pernah "
                    f"{aksi.kata_kerja_pasif}{tujuan}, jadi tidak bisa "
                    f"{aksi.kata_kerja_pasif} lagi."
                )
            else:
                pesan = (
                    f"{aksi.sebutan} {ref.entity_name} berstatus "
                    f"'{_STATUS_ID.get(status, status)}', jadi belum bisa "
                    f"{aksi.kata_kerja_pasif}{_ke}."
                )
            # N2 — SEBAB saja memindahkan beban berpikir ke owner. Langkahnya
            # dibaca dari tabel (satu tempat untuk seluruh keluarga), dan
            # kalimat yang menyebut perintah chat hanya dipakai bila aksi
            # penyedianya BENAR-BENAR terdaftar.
            try:
                from .direct_action_registry import DOCUMENT_ACTIONS_BY_KEY as _DAK
                for _lg in getattr(aksi, "langkah", ()):
                    if _lg.status != status:
                        continue
                    _tersedia = (not _lg.aksi_prasyarat) or (_lg.aksi_prasyarat in _DAK)
                    _teks = _lg.teks_chat if _tersedia else _lg.teks_dashboard
                    pesan += " " + _teks.replace("{nomor}", ref.entity_name)
                    break
            except Exception as _lg_err:  # noqa: BLE001
                logger.warning("[AKSI_DOK] langkah berikutnya gagal: %s", _lg_err)
            hasil.needs_clarification = True
            hasil.clarifications.append(pesan)
            return hasil

        payload = {"id": ref.entity_id, aksi.field_nomor: ref.entity_name}
        for kolom_db, field_payload in aksi.kolom_ringkas:
            if field_payload.startswith("_"):
                continue
            nilai = baris[kolom_db]
            payload[field_payload] = (
                float(nilai) if hasattr(nilai, "quantize") else nilai
            )
        if aksi.tabel_baris:
            n = await self.db.fetchval(
                f"SELECT count(*) FROM {aksi.tabel_baris} "
                f"WHERE {aksi.kolom_induk_baris} = $1",
                __import__("uuid").UUID(ref.entity_id),
            )
            payload["jumlah_baris"] = int(n or 0)

        hasil.payload = payload
        hasil.resolved = {aksi.entity_type: ref}
        return hasil

    async def _resolve_by_number(
        self,
        search_val: str,
        *,
        table: str,
        number_column: str,
        entity_type: str,
    ) -> Optional[ResolvedEntity]:
        """Generic document number resolver. Works with any table that has a
        number column (expense_number, journal_number, credit_note_number, etc.).

        Args:
            search_val: The document number to search (e.g. "EXP-2604-0016")
            table: DB table name (e.g. "expenses")
            number_column: Column containing the document number
            entity_type: Entity type label for the result
        """
        try:
            rows = await self.db.fetch(
                f"""SELECT id, {number_column}
                   FROM {table}
                   WHERE tenant_id = $1 AND {number_column} ILIKE $2
                   ORDER BY created_at DESC LIMIT 5""",
                self.tenant_id,
                f"%{search_val}%",
            )
            if not rows:
                # dok. 79 M1e — diperbaiki DI SUMBER, bukan di pemanggil.
                # Sebelum ini fungsi mengembalikan ResolvedEntity(entity_id="",
                # confidence=0.0) saat gagal: objek yang TRUTHY. Sebuah
                # `if entity:` lolos, lalu "" disubstitusi ke {id} dan lahir
                # POST /api/quotes//to-order. Fungsi ini punya NOL pemanggil
                # sampai commit ini, jadi mengubah kontraknya nol risiko
                # regresi — dan memperbaikinya di pemanggil berarti pemanggil
                # KEDUA akan mengulang kesalahan yang sama. Yang cacat bukan
                # pencariannya, melainkan kontrak yang mengundang salah baca.
                return None
            candidates = [{"id": str(r["id"]), "name": r[number_column]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type=entity_type,
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] %s lookup failed: %s", entity_type, e)
            return None

    # ── Response Entity Context (REC): Session-based resolution ──

    async def _resolve_account(self, name_fragment: str) -> "Optional[ResolvedEntity]":
        """Resolve CoA account by name. Excludes is_header=true (Law 18)."""
        try:
            rows = await self.db.fetch(
                """SELECT id, name, account_code, account_type
                   FROM chart_of_accounts
                   WHERE tenant_id = $1 AND is_header = false
                     AND is_active = true
                     AND name ILIKE $2
                   ORDER BY
                     CASE WHEN LOWER(name) = LOWER($3) THEN 0 ELSE 1 END,
                     name
                   LIMIT 5""",
                self.tenant_id,
                "%" + name_fragment + "%",
                name_fragment.strip(),
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="account",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [
                {"id": str(r["id"]), "name": r["name"] + " (" + r["account_code"] + ")"}
                for r in rows
            ]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for i, r in enumerate(rows):
                if r["name"].lower().strip() == name_fragment.lower().strip():
                    best = candidates[i]
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="account",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Account lookup failed: %s", e)
            return None

    @staticmethod
    async def _resolve_work_order(
        self, name_or_number: str
    ) -> "Optional[ResolvedEntity]":
        """Resolve work order by order_number or partial match."""
        try:
            _q = name_or_number.strip()
            rows = await self.db.fetch(
                "SELECT id::text, order_number, status "
                "FROM production_orders WHERE tenant_id = $1 AND order_number ILIKE $2 LIMIT 1",
                self.tenant_id,
                _q,
            )
            if not rows:
                rows = await self.db.fetch(
                    "SELECT id::text, order_number, status "
                    "FROM production_orders WHERE tenant_id = $1 AND order_number ILIKE $2 "
                    "ORDER BY created_at DESC LIMIT 1",
                    self.tenant_id,
                    f"%{_q}%",
                )
            if rows:
                row = rows[0]
                return ResolvedEntity(
                    entity_type="work_order",
                    entity_id=row["id"],
                    entity_name=row["order_number"],
                )
        except Exception as e:
            logger.warning(f"_resolve_work_order error: {e}")
        return None

    async def _resolve_bom(self, name_or_code: str) -> "Optional[ResolvedEntity]":
        """Resolve BOM by bom_code or bom_name."""
        try:
            _q = name_or_code.strip()
            rows = await self.db.fetch(
                "SELECT id::text, bom_code, bom_name, status "
                "FROM bill_of_materials WHERE tenant_id = $1 AND (bom_code ILIKE $2 OR bom_name ILIKE $2) LIMIT 1",
                self.tenant_id,
                _q,
            )
            if not rows:
                rows = await self.db.fetch(
                    "SELECT id::text, bom_code, bom_name, status "
                    "FROM bill_of_materials WHERE tenant_id = $1 AND (bom_code ILIKE $2 OR bom_name ILIKE $2) "
                    "ORDER BY created_at DESC LIMIT 1",
                    self.tenant_id,
                    f"%{_q}%",
                )
            if rows:
                row = rows[0]
                return ResolvedEntity(
                    entity_type="bom",
                    entity_id=row["id"],
                    entity_name=row["bom_code"] or row["bom_name"],
                )
        except Exception as e:
            logger.warning(f"_resolve_bom error: {e}")
        return None

    async def _resolve_work_center(
        self, name_or_code: str
    ) -> "Optional[ResolvedEntity]":
        """Resolve work center by code or name."""
        try:
            _q = name_or_code.strip()
            rows = await self.db.fetch(
                "SELECT id::text, code, name "
                "FROM work_centers WHERE tenant_id = $1 AND (code ILIKE $2 OR name ILIKE $2) AND is_active = true LIMIT 1",
                self.tenant_id,
                _q,
            )
            if not rows:
                rows = await self.db.fetch(
                    "SELECT id::text, code, name "
                    "FROM work_centers WHERE tenant_id = $1 AND (code ILIKE $2 OR name ILIKE $2) AND is_active = true "
                    "ORDER BY created_at DESC LIMIT 1",
                    self.tenant_id,
                    f"%{_q}%",
                )
            if rows:
                row = rows[0]
                return ResolvedEntity(
                    entity_type="work_center",
                    entity_id=row["id"],
                    entity_name=f"{row['code']} - {row['name']}",
                )
        except Exception as e:
            logger.warning(f"_resolve_work_center error: {e}")
        return None

    def resolve_from_session(user_text: str, session_state) -> dict:
        """Resolve pronouns and ordinals from REC session context.
        Returns dict of resolved fields to merge into extraction.entities.
        """
        t = user_text.lower()
        items = getattr(session_state, "last_response_items", None) or []
        entity = getattr(session_state, "active_entity", None)
        resolved = {}

        # Pronoun resolution
        _PRONOUNS = [
            " dia ",
            " mereka ",
            "nya?",
            "nya ",
            "ke mereka",
            "dari mereka",
            "di situ",
            "ke dia",
            "sama dia",
            " dia?",
            " dia,",
            " itu",
            " tersebut",
            " tadi",
        ]
        if entity and any(p in f" {t} " or t.endswith(p.strip()) for p in _PRONOUNS):
            _type = entity.get("type", "")
            _name = entity.get("name", "")
            if _type == "customer" and _name:
                resolved["customer_name"] = _name
            elif _type == "vendor" and _name:
                resolved["vendor_name"] = _name
            elif _type == "item" and _name:
                resolved["item_name"] = _name
            elif _type == "bank_account" and _name:
                resolved["bank_name"] = _name
            if entity.get("id"):
                resolved[f"{_type}_id"] = entity["id"]

        # Ordinal resolution
        if items:
            target = None
            if any(
                w in t for w in ["yang pertama", "pertama", "nomor 1", "no 1", "no. 1"]
            ):
                target = items[0]
            elif any(w in t for w in ["yang terakhir", "terakhir"]):
                target = items[-1]
            elif (
                any(w in t for w in ["yang kedua", "nomor 2", "no 2"])
                and len(items) > 1
            ):
                target = items[1]
            elif (
                any(w in t for w in ["yang ketiga", "nomor 3", "no 3"])
                and len(items) > 2
            ):
                target = items[2]
            elif any(
                w in t
                for w in [
                    "yang terbesar",
                    "terbesar",
                    "paling besar",
                    "paling gede",
                    "paling banyak",
                    "tergede",
                ]
            ):
                _with_amt = [i for i in items if i.get("_amount") is not None]
                if _with_amt:
                    target = max(_with_amt, key=lambda x: x["_amount"])
            elif any(
                w in t
                for w in [
                    "yang terkecil",
                    "terkecil",
                    "paling kecil",
                    "paling sedikit",
                    "paling dikit",
                ]
            ):
                _with_amt = [i for i in items if i.get("_amount") is not None]
                if _with_amt:
                    target = min(_with_amt, key=lambda x: x["_amount"])

            if target:
                resolved["_resolved_item"] = target
                # Set entity_id from the resolved item's document ID (for path param resolution)
                if target.get("_id"):
                    resolved["entity_id"] = target["_id"]
                if target.get("_ref"):
                    resolved["entity_name"] = target["_ref"]
                if target.get("_name") and not any(
                    resolved.get(k)
                    for k in ["customer_name", "vendor_name", "item_name"]
                ):
                    _domain = getattr(session_state, "last_domain", None)
                    if _domain in ("ar", "customer"):
                        resolved["customer_name"] = target["_name"]
                    elif _domain in ("ap", "vendor"):
                        resolved["vendor_name"] = target["_name"]
                    elif _domain == "items":
                        resolved["item_name"] = target["_name"]

        # Document reference matching — "EXP-2604-0016" / "INV-0042" / "PB-0001"
        # Scan last_response_items for _ref match when user mentions a doc number
        import re as _rec_re

        _doc_ref_match = _rec_re.search(
            r"\b(EXP|INV|PB|JE|CN|VC|QT|RP|BP|SA|BT|CD|VD)-[\w-]+\b",
            user_text,
            _rec_re.IGNORECASE,
        )
        if _doc_ref_match and items:
            _search_ref = _doc_ref_match.group(0).upper()
            for _item in items:
                _item_ref = (_item.get("_ref") or "").upper()
                if _item_ref and _search_ref in _item_ref:
                    resolved["_resolved_item"] = _item
                    if _item.get("_id"):
                        resolved["entity_id"] = _item["_id"]
                    if _item.get("_name"):
                        resolved["entity_name"] = _item["_name"]
                    break

        return resolved
