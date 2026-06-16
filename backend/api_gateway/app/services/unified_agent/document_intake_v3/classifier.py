"""Document Intake V3 — transfer type classifier (signals -> TransferType)."""

from __future__ import annotations

import asyncio
import logging
import re

from .primitives.arap_matcher import ARAPMatcher
from .primitives.bank_pair_detector import BankPairDetector
from .primitives.employee_matcher import EmployeeMatcher
from .signals import (
    ClassificationResult,
    Signal,
    classify_doc_number,
    extract_doc_numbers,
)
from .transfer_types import COMPATIBLE_PAIRS, TransferType

logger = logging.getLogger(__name__)


_RECEIPT_DOC_TYPES = {"receipt", "nota", "kwitansi", "payment_receipt"}
_TAX_DOC_TYPES = {"faktur_pajak", "tax_invoice"}

_CAPTION_SIGNALS: list[tuple[str, list[str], dict[TransferType, float]]] = [
    (
        "caption:payroll",
        ["gaji", "payroll", "salary", "thr", "bonus karyawan"],
        {TransferType.PAYROLL: 0.4},
    ),
    (
        "caption:tax",
        ["pajak", "pph", "ppn", "e-billing", "setor pajak"],
        {TransferType.TAX_PAYMENT: 0.4},
    ),
    ("caption:bpjs", ["bpjs", "iuran"], {TransferType.BPJS_PAYMENT: 0.5}),
    (
        "caption:receive",
        ["dari pelanggan", "terima dari", "pembayaran masuk", "pelanggan bayar"],
        {TransferType.RECEIVE_PAYMENT: 0.3},
    ),
    (
        "caption:bill",
        ["bayar ke", "bayar vendor", "ke supplier", "bayar supplier", "lunasi"],
        {TransferType.BILL_PAYMENT: 0.3},
    ),
    (
        "caption:loan",
        ["cicilan", "angsuran", "kta", "kredit bank"],
        {TransferType.LOAN_PAYMENT: 0.4},
    ),
    (
        "caption:owner_drawing",
        ["prive", "owner", "pribadi", "tarik modal"],
        {TransferType.OWNER_DRAWING: 0.4},
    ),
    (
        "caption:internal",
        ["antar rekening", "pindah ke", "internal transfer"],
        {TransferType.INTERNAL_TRANSFER: 0.4},
    ),
    (
        "caption:bank_fee",
        ["admin bank", "biaya transfer", "biaya admin"],
        {TransferType.BANK_FEE: 0.5},
    ),
    (
        "caption:expense",
        ["listrik", "internet", "sewa", "pdam", "air"],
        {TransferType.EXPENSE_OPERATIONAL: 0.4},
    ),
]


class TransferClassifier:
    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id
        self._arap = ARAPMatcher(pool, tenant_id)
        self._employees = EmployeeMatcher(pool, tenant_id)
        self._bank_pairs = BankPairDetector(pool, tenant_id)

    async def classify(
        self,
        ocr: dict,
        caption: str,
        tenant_ctx=None,
        extra_signals: list[Signal] | None = None,
    ) -> ClassificationResult:
        signals: list[Signal] = []
        signals += self._extract_ocr_signals(ocr or {})
        signals += self._extract_caption_signals(caption or "")
        signals += await self._extract_db_signals(ocr or {}, caption or "")
        if extra_signals:
            signals += list(extra_signals)

        scores: dict[TransferType, float] = {}
        for sig in signals:
            for t, w in sig.targets.items():
                scores[t] = scores.get(t, 0.0) + w
            for t, w in sig.excludes.items():
                scores[t] = scores.get(t, 0.0) - w

        scores = {k: max(0.0, min(1.0, v)) for k, v in scores.items()}

        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
        telemetry = [s.to_telemetry() for s in signals]

        if not top or top[0][1] == 0:
            return ClassificationResult(
                type=TransferType.UNKNOWN,
                confidence=0.0,
                alternatives=[],
                signals_fired=telemetry,
                reasons=["no_signals_or_all_zero"],
            )

        best_type, best_conf = top[0]
        second_conf = top[1][1] if len(top) > 1 else 0.0
        gap = best_conf - second_conf

        ambiguity_reason: str | None = None
        if best_conf < 0.5:
            ambiguity_reason = "low_conf"
        elif gap < 0.15:
            ambiguity_reason = "gap"
        elif self._has_conflict(best_type, scores):
            ambiguity_reason = "conflict"

        alternatives = [(t, s) for t, s in top]

        if ambiguity_reason:
            return ClassificationResult(
                type=TransferType.AMBIGUOUS,
                confidence=best_conf,
                alternatives=alternatives,
                signals_fired=telemetry,
                reasons=[
                    ambiguity_reason,
                    f"best={best_type.value}",
                    f"conf={best_conf:.2f}",
                    f"gap={gap:.2f}",
                ],
            )

        return ClassificationResult(
            type=best_type,
            confidence=best_conf,
            alternatives=alternatives,
            signals_fired=telemetry,
            reasons=[f"best={best_type.value}", f"conf={best_conf:.2f}"],
        )

    def _has_conflict(
        self, winning_type: TransferType, scores: dict[TransferType, float]
    ) -> bool:
        for other_type, other_score in scores.items():
            if other_type == winning_type or other_score < 0.4:
                continue
            if frozenset({winning_type, other_type}) in COMPATIBLE_PAIRS:
                continue
            return True
        return False

    # ------------------------------------------------------------------ OCR
    def _extract_ocr_signals(self, ocr: dict) -> list[Signal]:
        doc_type = (ocr.get("doc_type") or "").strip().lower()
        if not doc_type:
            return []

        if doc_type in _TAX_DOC_TYPES:
            return [
                Signal(
                    source="ocr",
                    name=f"doc_type={doc_type}",
                    strength=0.9,
                    targets={TransferType.TAX_PAYMENT: 0.9},
                    excludes={
                        TransferType.PAYROLL: 0.5,
                        TransferType.RECEIVE_PAYMENT: 0.5,
                        TransferType.BILL_PAYMENT: 0.5,
                    },
                )
            ]
        if doc_type in _RECEIPT_DOC_TYPES:
            return [
                Signal(
                    source="ocr",
                    name=f"doc_type={doc_type}",
                    strength=0.5,
                    targets={
                        TransferType.EXPENSE_OPERATIONAL: 0.5,
                        TransferType.BANK_FEE: 0.3,
                    },
                    excludes={
                        TransferType.PAYROLL: 0.5,
                        TransferType.TAX_PAYMENT: 0.3,
                        TransferType.LOAN_PAYMENT: 0.5,
                        TransferType.INTERNAL_TRANSFER: 0.8,
                    },
                )
            ]
        if doc_type == "bank_transfer":
            return [
                Signal(
                    source="ocr",
                    name="doc_type=bank_transfer",
                    strength=0.1,
                    targets={
                        TransferType.INTERNAL_TRANSFER: 0.1,
                        TransferType.RECEIVE_PAYMENT: 0.1,
                        TransferType.BILL_PAYMENT: 0.1,
                        TransferType.PAYROLL: 0.1,
                        TransferType.TAX_PAYMENT: 0.1,
                        TransferType.LOAN_PAYMENT: 0.1,
                        TransferType.OWNER_DRAWING: 0.1,
                        TransferType.BANK_FEE: 0.1,
                    },
                    excludes={TransferType.EXPENSE_OPERATIONAL: 0.3},
                )
            ]
        return []

    # ------------------------------------------------------------- Captions
    def _extract_caption_signals(self, caption: str) -> list[Signal]:
        if not caption:
            return []
        lowered = caption.lower()
        out: list[Signal] = []
        for name, keywords, targets in _CAPTION_SIGNALS:
            for kw in keywords:
                if re.search(rf"\b{re.escape(kw)}\b", lowered):
                    strength = max(targets.values()) if targets else 0.0
                    out.append(
                        Signal(
                            source="caption",
                            name=name,
                            strength=strength,
                            targets=dict(targets),
                        )
                    )
                    break
        return out

    # ------------------------------------------------------------------ DB
    async def _extract_db_signals(self, ocr: dict, caption: str) -> list[Signal]:
        counterparty = (
            ocr.get("counterparty_name")
            or ocr.get("vendor_name")
            or ocr.get("customer_name")
            or ""
        )
        amount = ocr.get("total_amount") or ocr.get("amount")
        src_acc = ocr.get("account_number_source") or ""
        dst_acc = ocr.get("account_number_destination") or ""

        tasks = [
            self._sig_own_accounts(src_acc, dst_acc),
            self._sig_counterparty_keywords(counterparty),
            self._sig_employee_match(counterparty),
            self._sig_customer_match(counterparty),
            self._sig_vendor_match(counterparty),
            self._sig_arap_amount(amount, counterparty),
            self._sig_caption_doc_number(caption),
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[IntakeV3Classifier] DB signal extraction timeout")
            return []

        signals: list[Signal] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("[IntakeV3Classifier] signal extraction error: %s", r)
                continue
            if r is None:
                continue
            if isinstance(r, list):
                signals.extend(r)
            else:
                signals.append(r)
        return signals

    async def _sig_own_accounts(self, src: str, dst: str) -> Signal | None:
        if not src and not dst:
            return None
        src_own, dst_own = await self._bank_pairs.detect_own_accounts(src, dst)
        if src_own and dst_own:
            return Signal(
                source="db",
                name="own_accounts_both_sides",
                strength=0.95,
                targets={TransferType.INTERNAL_TRANSFER: 0.95},
                excludes={
                    TransferType.RECEIVE_PAYMENT: 0.9,
                    TransferType.BILL_PAYMENT: 0.9,
                    TransferType.PAYROLL: 0.9,
                },
            )
        return None

    async def _sig_counterparty_keywords(
        self, counterparty: str
    ) -> list[Signal] | None:
        if not counterparty:
            return None
        up = counterparty.upper()
        out: list[Signal] = []
        if any(k in up for k in ("KPP", "DJP", "PAJAK")):
            out.append(
                Signal(
                    source="db",
                    name="counterparty_tax_authority",
                    strength=0.9,
                    targets={TransferType.TAX_PAYMENT: 0.9},
                    excludes={TransferType.BILL_PAYMENT: 0.5},
                )
            )
        if "BPJS" in up:
            out.append(
                Signal(
                    source="db",
                    name="counterparty_bpjs",
                    strength=0.9,
                    targets={TransferType.BPJS_PAYMENT: 0.9},
                    excludes={TransferType.BILL_PAYMENT: 0.5},
                )
            )
        if "LISTRIK" in up or "PLN" in up:
            out.append(
                Signal(
                    source="db",
                    name="counterparty_utility_pln",
                    strength=0.7,
                    targets={TransferType.EXPENSE_OPERATIONAL: 0.7},
                )
            )
        if any(k in up for k in ("TELKOM", "INDIHOME", "BIZNET")):
            out.append(
                Signal(
                    source="db",
                    name="counterparty_utility_telco",
                    strength=0.7,
                    targets={TransferType.EXPENSE_OPERATIONAL: 0.7},
                )
            )
        return out or None

    async def _sig_employee_match(self, counterparty: str) -> Signal | None:
        if not counterparty:
            return None
        hits = await self._employees.match_by_name(counterparty)
        if not hits:
            return None
        return Signal(
            source="db",
            name="counterparty_matches_employees",
            strength=0.7,
            targets={TransferType.PAYROLL: 0.7},
            excludes={
                TransferType.RECEIVE_PAYMENT: 0.5,
                TransferType.BILL_PAYMENT: 0.5,
            },
        )

    async def _sig_customer_match(self, counterparty: str) -> Signal | None:
        if not counterparty or not counterparty.strip():
            return None
        pattern = f"%{counterparty.strip()}%"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    self.tenant_id,
                )
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM customers
                    WHERE tenant_id = $1 AND is_active = true AND nama ILIKE $2
                    LIMIT 1
                    """,
                    self.tenant_id,
                    pattern,
                )
        if not row:
            return None
        return Signal(
            source="db",
            name="counterparty_matches_customer",
            strength=0.4,
            targets={
                TransferType.RECEIVE_PAYMENT: 0.4,
                TransferType.CUSTOMER_DEPOSIT: 0.2,
                TransferType.CUSTOMER_REFUND: 0.2,
            },
            excludes={TransferType.PAYROLL: 0.3},
        )

    async def _sig_vendor_match(self, counterparty: str) -> Signal | None:
        if not counterparty or not counterparty.strip():
            return None
        pattern = f"%{counterparty.strip()}%"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    self.tenant_id,
                )
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM vendors
                    WHERE tenant_id = $1 AND is_active = true AND name ILIKE $2
                    LIMIT 1
                    """,
                    self.tenant_id,
                    pattern,
                )
        if not row:
            return None
        return Signal(
            source="db",
            name="counterparty_matches_vendor",
            strength=0.4,
            targets={
                TransferType.BILL_PAYMENT: 0.4,
                TransferType.VENDOR_DEPOSIT: 0.2,
                TransferType.VENDOR_REFUND: 0.2,
            },
            excludes={TransferType.PAYROLL: 0.3},
        )

    async def _sig_arap_amount(self, amount, counterparty: str) -> list[Signal] | None:
        if amount is None:
            return None
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            return None
        if amt <= 0:
            return None
        amount_min = amt * 0.98
        amount_max = amt * 1.02
        cp = counterparty or ""
        ar_task = self._arap.match_ar(amount_min, amount_max, cp)
        ap_task = self._arap.match_ap(amount_min, amount_max, cp)
        ar_res, ap_res = await asyncio.gather(ar_task, ap_task, return_exceptions=True)
        out: list[Signal] = []
        if isinstance(ar_res, list) and ar_res:
            out.append(
                Signal(
                    source="db",
                    name="amount_matches_open_invoice_AR",
                    strength=0.6,
                    targets={TransferType.RECEIVE_PAYMENT: 0.6},
                )
            )
        if isinstance(ap_res, list) and ap_res:
            out.append(
                Signal(
                    source="db",
                    name="amount_matches_open_bill_AP",
                    strength=0.6,
                    targets={TransferType.BILL_PAYMENT: 0.6},
                )
            )
        return out or None

    async def _sig_caption_doc_number(self, caption: str) -> list[Signal] | None:
        """FIX_DOCNUM_MATCH: an explicit invoice/bill number in the caption that
        resolves to an OPEN document is the strongest possible signal — it fixes
        both the transfer type AND the direction (INV→receivable→in, PB→payable→
        out), so the user usually won't even see a direction pill. Caption-only by
        design → parity tests (caption="") never fire this → V2/V3 parity preserved.
        """
        nums = extract_doc_numbers(caption or "")
        if not nums:
            return None
        out: list[Signal] = []
        for n in nums:
            kind = classify_doc_number(n)
            if kind == "ar":
                hits = await self._arap.match_ar_by_number(n)
                if hits:
                    out.append(
                        Signal(
                            source="db",
                            name="caption_invoice_number_AR",
                            strength=0.9,
                            targets={TransferType.RECEIVE_PAYMENT: 0.9},
                            excludes={
                                TransferType.BILL_PAYMENT: 0.6,
                                TransferType.INTERNAL_TRANSFER: 0.6,
                                TransferType.PAYROLL: 0.6,
                            },
                        )
                    )
            elif kind == "ap":
                hits = await self._arap.match_ap_by_number(n)
                if hits:
                    out.append(
                        Signal(
                            source="db",
                            name="caption_bill_number_AP",
                            strength=0.9,
                            targets={TransferType.BILL_PAYMENT: 0.9},
                            excludes={
                                TransferType.RECEIVE_PAYMENT: 0.6,
                                TransferType.INTERNAL_TRANSFER: 0.6,
                                TransferType.PAYROLL: 0.6,
                            },
                        )
                    )
        return out or None
