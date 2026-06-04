from goldset.schema import (
    Tier,
    Category,
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
    GoldCase(
        "lookup_profit_this_month",
        Category.LOOKUP,
        [
            Turn(
                "profit bulan ini",
                [
                    (A_INTENT_IN, ["query_profit_loss"]),
                    (A_TEXT_NOT_CONTAINS, "Penjualan Outstanding"),
                ],
            )
        ],
        why="Bug I: must be P&L, not a mislabeled AR figure",
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
        [Turn("kenapa cash flow saya seret bulan ini?", [(A_TIER, Tier.B)])],
        why="P0<->P2 seam: must engage with 'why' via contributing facts, not silence or a made-up single cause",
    ),
    GoldCase(
        "why_profit_down",
        Category.WHY,
        [Turn("kenapa untung saya turun ya bulan ini?", [(A_TIER, Tier.B)])],
        why="causal question without a guaranteed InsightEngine rule",
    ),
    # ---- FOLLOWUP / multi-turn (context) ----
    GoldCase(
        "followup_ar_top_value",
        Category.FOLLOWUP,
        [
            Turn(
                "siapa pelanggan dengan piutang terbesar?",
                [(A_INTENT_IN, ["query_ar_invoices", "query_ar_outstanding"])],
            ),
            Turn("berapa nilainya?", [(A_TEXT_CONTAINS, "Rp")]),
        ],
        why="pronoun/ordinal follow-up must resolve from session state, not re-ask",
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
                    (A_TEXT_NOT_CONTAINS, "Margin Keuntungan per Produk"),
                ],
            )
        ],
        why="TRAP: why-question with no guaranteed rule; must give contributing facts, not a margin dump or a single fabricated cause",
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
]
