from goldset.schema import (
    Tier,
    Category,
    QueryClass,
    Turn,
    GoldCase,
    A_INTENT_IN,
    A_TIER,
    A_TEXT_CONTAINS,
    A_TEXT_CONTAINS_ANY,
    A_TEXT_NOT_CONTAINS,
    A_HAS_TRACE,
    A_IS_CONFIRMATION,
)

CASES = [
    # ---- LOOKUP (Tier A) ----
    GoldCase(
        "lookup_customers",
        Category.LOOKUP,
        [
            Turn(
                "daftar pelanggan",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["query_customers_list"])],
            )
        ],
    ),
    GoldCase(
        "lookup_avg_price",
        Category.LOOKUP,
        [
            Turn(
                "rata-rata harga jual",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["calc_avg_harga_jual"])],
            )
        ],
    ),
    GoldCase(
        "lookup_bank_list",
        Category.LOOKUP,
        [
            Turn(
                "daftar rekening bank",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["query_bank_accounts_list"])],
            )
        ],
    ),
    GoldCase(
        "lookup_ar",
        Category.LOOKUP,
        [
            Turn(
                "total piutang berapa",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["query_ar_outstanding"])],
            )
        ],
        why="piutang is a stock (point-in-time) figure → answer directly; clarify = over_clarify fail",
        query_class=QueryClass.STOCK,
    ),
    GoldCase(
        "lookup_ap",
        Category.LOOKUP,
        [
            Turn(
                "total utang usaha saya berapa",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["query_ap_outstanding"])],
            )
        ],
        why="hutang is a stock (point-in-time) figure → answer directly; clarify = over_clarify fail",
        query_class=QueryClass.STOCK,
    ),
    GoldCase(
        "lookup_items_list",
        Category.LOOKUP,
        [
            Turn(
                "daftar barang saya",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["query_items_list"])],
            )
        ],
    ),
    # NOTE: removed `lookup_profit_this_month` — duplicate stimulus of
    # `adv_profit_bulan_ini_not_ar` (both query "profit bulan ini"). Kept the
    # adversarial one (tests the "not AR" trap → more valuable).
    # ---- FLOW (income-statement / period-bound) lookups: clarify-first acceptable ----
    GoldCase(
        "lookup_profit_ambiguous",
        Category.LOOKUP,
        [
            Turn(
                "berapa laba saya?",
                [(A_TEXT_CONTAINS_ANY, ["periode", "bulan", "rentang", "Rp", "tahun"])],
            )
        ],
        why="flow/period query without a period → clarify-first acceptable (stock/flow rule, charter READ-ambiguous)",
        query_class=QueryClass.FLOW,
    ),
    GoldCase(
        "lookup_omzet_ambiguous",
        Category.LOOKUP,
        [
            Turn(
                "omzet saya berapa?",
                [(A_TEXT_CONTAINS_ANY, ["periode", "bulan", "rentang", "Rp", "tahun"])],
            )
        ],
        why="flow/period query without a period → clarify-first acceptable",
        query_class=QueryClass.FLOW,
    ),
    # ---- CRUD (Tier A, preview only) ----
    GoldCase(
        "crud_create_customer",
        Category.CRUD,
        [
            Turn(
                "buat pelanggan baru Toko Goldset",
                [
                    (A_TIER, Tier.A),
                    (A_IS_CONFIRMATION, True),
                    (A_INTENT_IN, ["create_customer"]),
                ],
            )
        ],
        why="must propose a confirmation card, never auto-post (I3)",
    ),
    GoldCase(
        "crud_create_vendor",
        Category.CRUD,
        [
            Turn(
                "tambah vendor baru PT Goldset Supply",
                [(A_IS_CONFIRMATION, True), (A_INTENT_IN, ["create_vendor"])],
            )
        ],
    ),
    GoldCase(
        "crud_create_item",
        Category.CRUD,
        [
            Turn(
                "bikin barang baru Kaos Goldset harga jual 50000",
                [(A_IS_CONFIRMATION, True), (A_INTENT_IN, ["create_item"])],
            )
        ],
    ),
    # ---- REASONING (Tier B, open-ended analytical) ----
    GoldCase(
        "reason_worst_margin_product",
        Category.REASONING,
        [Turn("produk mana yang paling boncos buat saya?", [(A_TIER, Tier.B)])],
        why="open-ended analytical ranking, must not collapse to a static lookup",
    ),
    GoldCase(
        "reason_slowest_payer",
        Category.REASONING,
        [Turn("pelanggan mana yang paling sering telat bayar?", [(A_TIER, Tier.B)])],
    ),
    GoldCase(
        "reason_expense_driver",
        Category.REASONING,
        [Turn("pengeluaran terbesar saya lari ke mana sih?", [(A_TIER, Tier.B)])],
    ),
    # ---- WHATIF / projection (Tier B) ----
    GoldCase(
        "whatif_omzet_up_100",
        Category.WHATIF,
        [
            Turn(
                "jika omzet penjualan saya naik 100 persen bulan depan, berapa kira-kira laba kotor saya, berdasarkan data 2 bulan terakhir?",
                [
                    (A_TIER, Tier.B),
                    (A_INTENT_IN, ["query_gross_profit_projection"]),
                    (A_TEXT_CONTAINS_ANY, ["asumsi", "Asumsi"]),
                    (A_HAS_TRACE, True),
                ],
            )
        ],
        why="canonical projection; needs window + assumptions (mandatory projection rule)",
    ),
    GoldCase(
        "whatif_omzet_down_30",
        Category.WHATIF,
        [
            Turn(
                "kalau penjualan turun 30 persen, laba kotor saya jadi berapa?",
                [
                    (A_TIER, Tier.B),
                    (A_INTENT_IN, ["query_gross_profit_projection"]),
                    (A_TEXT_CONTAINS_ANY, ["asumsi", "Asumsi"]),
                ],
            )
        ],
    ),
    GoldCase(
        "whatif_estimate_50",
        Category.WHATIF,
        [
            Turn(
                "estimasi keuntungan kotor kalau omzet naik 50%",
                [(A_INTENT_IN, ["query_gross_profit_projection"]), (A_TIER, Tier.B)],
            )
        ],
    ),
    # ---- WHY (Tier B; no rule -> ranked contributing facts, no single fabricated cause) ----
    GoldCase(
        "why_cashflow_tight",
        Category.WHY,
        [
            Turn(
                "kenapa cash flow saya seret bulan ini?",
                [
                    (A_TIER, Tier.B),
                    (A_INTENT_IN, ["query_business_drivers"]),
                    (A_HAS_TRACE, True),
                    (
                        A_TEXT_CONTAINS_ANY,
                        [
                            "hutang",
                            "piutang",
                            "kas",
                            "beban",
                            "omzet",
                            "pendapatan",
                            "%",
                            "vs",
                        ],
                    ),
                    # forbid the old static-margin dump and any fabricated single cause
                    (A_TEXT_NOT_CONTAINS, "Margin Keuntungan per Produk"),
                    (A_TEXT_NOT_CONTAINS, "penyebab utama"),
                ],
            )
        ],
        why="P0<->P2 seam: must engage with 'why' via journal-derived contributing facts (ranked drivers + period trace), not silence or a made-up single cause",
    ),
    GoldCase(
        "why_profit_down",
        Category.WHY,
        [
            Turn(
                "kenapa untung saya turun ya bulan ini?",
                [
                    (A_TIER, Tier.B),
                    (A_INTENT_IN, ["query_business_drivers"]),
                    (A_HAS_TRACE, True),
                    (
                        A_TEXT_CONTAINS_ANY,
                        [
                            "hutang",
                            "piutang",
                            "kas",
                            "beban",
                            "omzet",
                            "pendapatan",
                            "%",
                            "vs",
                        ],
                    ),
                    (A_TEXT_NOT_CONTAINS, "Margin Keuntungan per Produk"),
                    (A_TEXT_NOT_CONTAINS, "penyebab utama"),
                ],
            )
        ],
        why="causal question without a guaranteed InsightEngine rule -> ranked contributing facts (structure asserted, not exact deltas; sparse partial-month tenant)",
    ),
    # ---- FOLLOWUP / multi-turn (context) ----
    GoldCase(
        "followup_ar_top_value",
        Category.FOLLOWUP,
        [
            Turn(
                "siapa pelanggan dengan piutang terbesar?",
                [
                    (
                        A_INTENT_IN,
                        [
                            "query_ar_invoices",
                            "query_ar_outstanding",
                            "query_ar_by_customer",
                        ],
                    )
                ],
            ),
            Turn("berapa nilainya?", [(A_TEXT_CONTAINS, "Rp")]),
        ],
        why="pronoun/ordinal follow-up must resolve from session state, not re-ask",
        # Turn 1 ("piutang terbesar") is a point-in-time AR (stock) ranking →
        # natural answer = current balance snapshot; a period-clarify here is over_clarify.
        query_class=QueryClass.STOCK,
    ),
    GoldCase(
        "followup_domain_carry",
        Category.FOLLOWUP,
        [
            Turn(
                "utang saya ke vendor berapa total?",
                [(A_INTENT_IN, ["query_ap_outstanding"])],
            ),
            Turn("yang paling besar siapa?", [(A_TEXT_NOT_CONTAINS, "maksud Anda")]),
        ],
        why="short follow-up must stay in the AP domain, not bounce to clarification",
        # Turn 1 ("utang ke vendor berapa total") is vendor-AP outstanding =
        # point-in-time stock balance → DIRECT expected; live bot over-clarifies it.
        query_class=QueryClass.STOCK,
    ),
    GoldCase(
        "followup_pronoun_customer",
        Category.FOLLOWUP,
        [
            Turn(
                "ada berapa pelanggan saya?",
                [
                    (
                        A_INTENT_IN,
                        [
                            "calc_count_customers_active",
                            "query_customers_summary",
                            "query_customers_list",
                        ],
                    )
                ],
            ),
            Turn("yang dari Manado berapa?", [(A_TEXT_NOT_CONTAINS, "tidak mengerti")]),
        ],
    ),
    # ---- ADVERSARIAL (the traps; owner-mandated) ----
    GoldCase(
        "adv_whatif_disguised_as_lookup",
        Category.ADVERSARIAL,
        [
            Turn(
                "laba kotor saya berapa kalau jualan naik dua kali lipat?",
                [
                    (A_INTENT_IN, ["query_gross_profit_projection"]),
                    (A_TIER, Tier.B),
                    (A_TEXT_NOT_CONTAINS, "Margin Keuntungan per Produk"),
                ],
            )
        ],
        why="TRAP: opens like a 'laba kotor berapa' lookup but is a what-if; must NOT hit the static margin table (the original bug)",
    ),
    GoldCase(
        "adv_margin_keyword_is_projection",
        Category.ADVERSARIAL,
        [
            Turn(
                "kalau margin saya tetap, kira-kira untung kotor bulan depan berapa?",
                [(A_INTENT_IN, ["query_gross_profit_projection"]), (A_TIER, Tier.B)],
            )
        ],
        why="TRAP: contains 'margin' keyword that lures calc_profit_margin_per_item, but it is a projection",
    ),
    GoldCase(
        "adv_terlaris_not_projection",
        Category.ADVERSARIAL,
        [
            Turn(
                "produk apa yang paling laku bulan ini?",
                [
                    (A_INTENT_IN, ["calc_top_selling_items", "query_items_summary"]),
                    (A_TEXT_NOT_CONTAINS, "asumsi"),
                ],
            )
        ],
        why="TRAP (reverse): a genuine lookup that must NOT be over-escalated to projection just because it's product-analytical",
    ),
    GoldCase(
        "adv_profit_bulan_ini_not_ar",
        Category.ADVERSARIAL,
        [
            Turn(
                "profit bulan ini",
                [
                    (A_INTENT_IN, ["query_profit_loss"]),
                    (A_TEXT_NOT_CONTAINS, "Penjualan Outstanding"),
                ],
            )
        ],
        why="TRAP (Bug I): profit must be P&L, not a mislabeled AR/outstanding figure",
    ),
    GoldCase(
        "adv_why_without_rule",
        Category.ADVERSARIAL,
        [
            Turn(
                "kenapa pengeluaran saya membengkak bulan ini?",
                [
                    (A_TIER, Tier.B),
                    (A_INTENT_IN, ["query_business_drivers"]),
                    (A_HAS_TRACE, True),
                    (
                        A_TEXT_CONTAINS_ANY,
                        [
                            "hutang",
                            "piutang",
                            "kas",
                            "beban",
                            "omzet",
                            "pendapatan",
                            "%",
                            "vs",
                        ],
                    ),
                    (A_TEXT_NOT_CONTAINS, "Margin Keuntungan per Produk"),
                    (A_TEXT_NOT_CONTAINS, "penyebab utama"),
                ],
            )
        ],
        why="TRAP: why-question with no guaranteed rule; must give journal-derived contributing facts (ranked drivers + period trace), not a margin dump or a single fabricated cause",
    ),
    GoldCase(
        "adv_rugi_keyword_lookup",
        Category.ADVERSARIAL,
        [
            Turn(
                "laporan laba rugi bulan ini",
                [
                    (A_INTENT_IN, ["query_profit_loss"]),
                    (A_TIER, Tier.A),
                    (A_TEXT_NOT_CONTAINS, "asumsi"),
                ],
            )
        ],
        why="TRAP: a plain P&L report (Tier A) must NOT be escalated to projection just for sharing 'laba/rugi' tokens",
    ),
    GoldCase(
        "adv_count_not_projection",
        Category.ADVERSARIAL,
        [
            Turn(
                "kalau dihitung, ada berapa total pelanggan saya sekarang?",
                [(A_TIER, Tier.A), (A_TEXT_NOT_CONTAINS, "asumsi")],
            )
        ],
        why="TRAP: 'kalau dihitung' looks conditional but is a plain count, must stay Tier A",
    ),
    GoldCase(
        "lookup_customer_sales_rank",
        Category.LOOKUP,
        [
            Turn(
                "siapa pelanggan paling loyal 30 hari terakhir?",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["calc_rank_customers_by_sales"])],
            )
        ],
        why="dogfood #1: was a fabricated loyalty ranking (I1/I5 bluff); now journal-derived calc_rank_customers_by_sales",
    ),
    GoldCase(
        "lookup_customer_sales_single",
        Category.LOOKUP,
        [
            Turn(
                "total nilai pembelian pelanggan Debora 30 hari terakhir",
                [(A_TIER, Tier.A), (A_INTENT_IN, ["query_customer_sales"])],
            )
        ],
        why="dogfood #3: was misrouted to query_customer_ar; now query_customer_sales (customer-keyword-gated)",
    ),
    GoldCase(
        "lookup_customer_sales_named_no_keyword",  # FIX_DOGFOOD_CUSTSALES_NAMED
        Category.LOOKUP,
        [
            Turn(
                "Berapa total pembelian Aneke Mataputun dalam 30 hari terakhir?",
                [
                    (A_TIER, Tier.A),
                    (A_INTENT_IN, ["query_customer_sales"]),
                    (A_TEXT_NOT_CONTAINS, "belum tercatat"),
                ],
            )
        ],
        why="dogfood 2026-06-08 #1: customer named DIRECTLY (no pelanggan/customer keyword) -> regex gate fails -> Gemini hijacks to calc_sum_purchases_this_month / query_customer_ar; resolve-then-route override fires on name->customer resolution.",
    ),
    GoldCase(
        "drill_ar_invoice_detail_named_customer",  # FIX_DOGFOOD_AR_DRILL_OUTSTANDING
        Category.FOLLOWUP,
        [
            Turn(
                "apa piutang yang paling besar dan minta rincian pelanggan yang punya piutang terbesar",
                [(A_INTENT_IN, ["query_ar_by_customer", "calc_rank_customers_by_ar"])],
            ),
            Turn(
                "minta rincian faktur piutang dari pelanggan Aqua Airmadidi",
                [
                    # Bug: drilldown_table mapped AR-by-customer to the generic
                    # query_sales_invoices_list path -> every invoice Rp 0,
                    # Total Rp 0, customer scope lost (20 unrelated invoices).
                    (A_TEXT_NOT_CONTAINS, "Total: Rp 0"),
                    (A_TEXT_NOT_CONTAINS, "dari 20 item"),
                    # Must surface the customer real outstanding magnitude
                    # (journal-derived per-invoice piutang for Aqua).
                    (A_TEXT_CONTAINS, "16.170.000"),
                ],
            ),
        ],
        why="dogfood 2026-06-08 #2: AR-by-customer drill ('rincian faktur piutang dari pelanggan Aqua') was intercepted by DRILL_GUARD -> drilldown_table -> generic query_sales_invoices_list -> Rp 0 per invoice (wrong field + lost customer scope). Now renders per-invoice journal-derived outstanding for the resolved customer (draft/void excluded, outstanding>0 only).",
    ),
    # ---- FIX_DOGFOOD_OVERDUE_PRIORITY_LIST (2026-06-08) ----
    GoldCase(
        "overdue_ap_priority_list",  # FIX_DOGFOOD_OVERDUE_PRIORITY_LIST
        Category.LOOKUP,
        [
            Turn(
                "mana tagihan hutang vendor yang paling mendesak harus dibayar?",
                [
                    (
                        A_INTENT_IN,
                        [
                            "query_bills_overdue",
                            "query_ap_aging",
                            "query_ap_outstanding",
                        ],
                    ),
                    # Bug: answered as an AP SUMMARY ("Ringkasan Tagihan Vendor",
                    # cumulative Total Hutang/Total Jatuh Tempo) instead of a
                    # prioritized per-bill list to act on.
                    (A_TEXT_NOT_CONTAINS, "Ringkasan"),
                    # Must be a prioritized per-item overdue list, journal-derived.
                    (A_TEXT_CONTAINS, "jatuh tempo"),
                    (A_TEXT_CONTAINS, "hari"),
                    # Reconciling total of the real overdue set (39 bills /
                    # Rp 110.481.345, journal-derived via compute_ap_outstanding).
                    (A_TEXT_CONTAINS, "110.481.345"),
                ],
            ),
        ],
        why="dogfood 2026-06-08 #3 (Theme B): AP urgency 'tagihan vendor paling mendesak harus dibayar' was answered as a cumulative SUMMARY/aging, not a prioritized per-bill list. Now renders a deterministic most-overdue-first list (party + bill# + journal-derived outstanding + due date + days overdue), draft/void excluded, outstanding>0, top-15 + '+N lainnya' + reconciling total. Iron Law 1 by construction (no LLM polish).",
    ),
    GoldCase(
        "overdue_ar_priority_list",  # FIX_DOGFOOD_OVERDUE_PRIORITY_LIST
        Category.LOOKUP,
        [
            Turn(
                "Piutang apa saja yang sudah jatuh tempo ya dan harus saya tagih mendesak",
                [
                    (
                        A_INTENT_IN,
                        [
                            "query_sales_invoices_overdue",
                            "query_ar_aging",
                            "query_ar_invoices",
                            "query_ar_outstanding",
                        ],
                    ),
                    # Bug A: answered as an AR AGING SUMMARY (bucket "1-30",
                    # "31-60"). Bug B (worse): generic list path let Gemini polish
                    # HALLUCINATE the total ("50 faktur total Rp -19.155.000" vs
                    # real 95 / Rp 79.371.000) — Iron Law 1 violation.
                    (A_TEXT_NOT_CONTAINS, "1-30"),
                    (A_TEXT_NOT_CONTAINS, "Ringkasan"),
                    (A_TEXT_CONTAINS, "jatuh tempo"),
                    (A_TEXT_CONTAINS, "hari"),
                    # Reconciling total of the real overdue set (95 invoices /
                    # Rp 79.371.000, journal-derived via compute_ar_outstanding).
                    (A_TEXT_CONTAINS, "79.371.000"),
                ],
            ),
        ],
        why="dogfood 2026-06-08 #4 (Theme B): AR urgency 'piutang jatuh tempo harus saya tagih mendesak' was answered as an aging SUMMARY or (generic-list path) an LLM-hallucinated total (-19.155.000 / 50 faktur). Now renders a deterministic most-overdue-first per-invoice list (customer + invoice# + journal-derived outstanding + due date + days overdue), draft/void excluded, outstanding>0, top-15 + '+N lainnya' + reconciling total (95 / Rp 79.371.000). Iron Law 1 by construction.",
    ),
    # ---- FIX_DOGFOOD_BILL_DUEDATE (2026-06-09) ----
    GoldCase(
        "crud_create_bill_proposes_no_duedate_ask",  # FIX_DOGFOOD_BILL_DUEDATE
        Category.CRUD,
        [
            Turn(
                "Catat faktur pembelian dari Knitto Textile Holis, 100,5 meter Kain Taslan @ 22rb",
                [
                    # Must PROPOSE the confirmation card directly, mirroring
                    # create_sales_invoice. due_date is DERIVED from vendor
                    # payment_terms_days (Knitto = NET-30), never asked.
                    (A_IS_CONFIRMATION, True),
                    (A_INTENT_IN, ["create_bill"]),
                    # The bug emitted a TEXT clarification asking for jatuh tempo.
                    (A_TEXT_NOT_CONTAINS, "jatuh tempo"),
                ],
            ),
        ],
        why="dogfood 2026-06-09 (Knitto Textile Holis): bill create echoed vendor+item+qty 100.5+harga 22rb correctly but INTERMITTENTLY blocked with a TEXT clarification asking for jatuh tempo instead of proposing. Root cause: Stage-2 LLM sometimes returned the json-typed items field as an empty STRING for create_bill; entity_resolver._build_payload guard treated empty string as present so the scalar items-build was skipped, validate_payload saw falsy items and set needs_clarification (mis-narrated by the clarification LLM as a due_date question). Fix: guard now uses not payload.get(items) so empty items always trigger the build; bill proposes directly with due derived from vendor NET-30. Also preserved decimal qty 100.5 in _enrich_purchase_invoice (was int-truncated to 100).",
    ),
    # ---- FIX_DOGFOOD_RESTOCK_PRIORITY (2026-06-08) ----
    GoldCase(
        "restock_priority_compound",  # FIX_DOGFOOD_RESTOCK_PRIORITY
        Category.LOOKUP,
        [
            Turn(
                "item barang apa yang penjualannya bagus tapi stok-nya menipis?",
                [
                    (A_INTENT_IN, ["query_restock_priority"]),
                    # Must be the INTERSECTION ranked by sales velocity, not a
                    # plain stok-0 dump. Kaos is the #1 seller (stok 0) -> present.
                    (A_TEXT_CONTAINS, "Kaos"),
                    # Non-sellers (0 units sold in 90d) MUST be absent: the old
                    # behavior listed every stok-0 item incl. "Kopi Coba".
                    (A_TEXT_NOT_CONTAINS, "Kopi Coba"),
                ],
            ),
        ],
        why="dogfood 2026-06-08: compound 'penjualan bagus tapi stok menipis' answered ONLY the low-stock half (plain stok-0 list incl. zero-sales 'Kopi Coba'/E2E test items; misrouted to UNREGISTERED query_items_top_products). Now query_restock_priority renders the INTERSECTION (sells well last 90d AND stock <= reorder_level / <= 0) ranked by 90d units sold DESC, journal/ledger-derived (Iron Law 1+16 by construction). Kaos (#1 seller, stok 0) leads; non-sellers excluded.",
    ),
    # ---- FIX_DOGFOOD_RECEIVEPAY_RESOLVE (2026-06-09) ----
    GoldCase(
        "receive_payment_pelunasan_piutang",  # FIX_DOGFOOD_RECEIVEPAY_RESOLVE
        Category.CRUD,
        [
            Turn(
                "Catat pelunasan piutang dari Aqua Airmadidi sebesar 16.170.000 lewat transfer bank",
                [
                    (A_TIER, Tier.A),
                    # THE BUG: bot asked the user for raw internal UUIDs
                    # ("Customer ID", "Bank Account ID") + a payment date that
                    # should default to today. Hidden FieldSpec labels MUST
                    # NEVER be surfaced to the user.
                    (A_TEXT_NOT_CONTAINS, "Customer ID"),
                    (A_TEXT_NOT_CONTAINS, "Bank Account ID"),
                    # Correct end state: receive-payment for the resolved
                    # customer Aqua, the stated amount, asking the bank by NAME
                    # (3-tier pill picker, >1 active account in grapgrap).
                    (A_TEXT_CONTAINS, "Aqua Airmadidi"),
                    (A_TEXT_CONTAINS, "16.170.000"),
                    (A_TEXT_CONTAINS, "rekening"),
                ],
            )
        ],
        # Intent is intentionally NOT asserted: this turn returns a CLARIFICATION
        # (bank-name pills) via the early-override -> _handle_pipeline path, which
        # carries no tool_calls / action_key and does not write the chosen intent
        # to intent_decision_log (the stale DB row still reads create_bank_transfer
        # from the legacy crud_guard), so A_INTENT_IN is not cleanly observable
        # here. The load-bearing assertion is the TEXT: no raw-ID ask, correct
        # customer + amount, bank asked by name.
        why="dogfood 2026-06-09: 'Catat pelunasan piutang dari <pelanggan> sebesar <X> lewat transfer bank' (a) misrouted to create_bank_transfer ('transfer bank' hijack via crud_guard; LLM router flip-flop) and (b) leaked hidden FieldSpec ID labels to the user ('Customer ID', 'Bank Account ID') + asked for a date that should default to today. Fix: early override routes 'pelunasan/lunasi/bayar piutang dari <customer>' to create_receive_payment (customer resolved by name), the generic hidden/display_only gate (validate_payload + _natural_clarification + _execute_propose_direct) never surfaces hidden IDs, the no-hint 3-tier bank picker asks by NAME, payment_date defaults to today, and allocations are built journal-derived from compute_ar_outstanding (INV-2605-0009 / Rp 16.170.000). Real bank-to-bank transfer ('transfer dari BCA ke Mandiri') is untouched.",
    ),
    # ---- FIX_RENAME_MENJADI (2026-06-13) ----
    GoldCase(
        "crud_update_item_rename_via_menjadi",  # FIX_RENAME_MENJADI
        Category.CRUD,
        [
            Turn(
                "edit item Lacoste Pique 24s Hitam Ecer menjadi Lacoste Pique 30s Putih",
                [
                    (A_TIER, Tier.A),
                    (A_IS_CONFIRMATION, True),
                    (A_INTENT_IN, ["update_item"]),
                    # The new name (after "menjadi") MUST be captured as the
                    # proposed name and shown on the card.
                    (A_TEXT_CONTAINS, "Lacoste Pique 30s Putih"),
                    # REGRESSION GUARD: the old behavior leaked the OLD name into
                    # Deskripsi and left an empty change-set -> auto-cancel. The
                    # cancel narration must NOT appear; this is a live rename.
                    (A_TEXT_NOT_CONTAINS, "tidak ada perubahan"),
                ],
            )
        ],
        why="FIX_RENAME_MENJADI 2026-06-13 (commit 3b738573): 'edit item X menjadi Y' lost Y. classify_crud_intent truncated at menjadi/jadi/ke and discarded the value after it, so the new name Y was dropped, the old name X leaked into Deskripsi, and the empty change-set auto-cancelled. Fix: rename-detection before truncation + colon strip + 4-tuple new_value propagated to a dedicated rename fast-path that maps Y to the registry entity_name_field (name/account_name), forces X as the lookup key, and scrubs any leaked old name from description/deskripsi/notes. Covers item/customer/vendor/bank/warehouse. Must propose a confirmation card (I3, never auto-post) showing the NEW name.",
    ),
    GoldCase(
        "crud_update_vendor_fieldset_not_rename",  # FIX_RENAME_MENJADI (false-positive guard)
        Category.CRUD,
        [
            Turn(
                "edit vendor Knitto Textile Holis, ubah telepon jadi 081234567890",
                [
                    (A_TIER, Tier.A),
                    (A_IS_CONFIRMATION, True),
                    (A_INTENT_IN, ["update_vendor"]),
                    # Field-set, NOT a rename: the phone number must land on the
                    # telepon/phone field and the vendor NAME must stay Knitto.
                    (A_TEXT_CONTAINS, "081234567890"),
                    (A_TEXT_CONTAINS, "Knitto"),
                    # GUARD: bare "jadi" preceded by a field keyword (telepon)
                    # must NOT be mistaken for a rename to "081234567890".
                    (A_TEXT_NOT_CONTAINS, "tidak ada perubahan"),
                ],
            )
        ],
        why="FIX_RENAME_MENJADI 2026-06-13 false-positive guard: 'edit vendor X, ubah telepon jadi 081...' is a FIELD-SET, not a rename. The rename block only fires for connector 'menjadi' (no field keyword on the left) or bare 'jadi'/'ke' when NEITHER side carries a field keyword; here 'telepon' precedes 'jadi' so rename detection is suppressed and the phone is set normally with the vendor name unchanged. Proves the rename fix did not introduce a rename false-positive on field-set phrasing.",
    ),
]
