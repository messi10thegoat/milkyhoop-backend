"""
Tier 2: Chat Intent → Preference detection.

Detects when user explicitly sets, queries, or deletes preferences via chat.
Runs BEFORE normal intent routing — if match, short-circuits with confirmation.

Regex-based (deterministic, 0ms). No LLM needed.
"""

import re
import logging
from typing import Optional

from .preference_manager import PreferenceManager, LABEL_MAP

logger = logging.getLogger("unified_agent.preference_detector")

# Display name patterns
_DISPLAY_NAME_PATTERNS = [
    re.compile(r"(?:panggil|sapa)\s+(?:saya|aku|gue|gw)\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:nama|panggilan)\s+(?:saya|aku|gue|gw)\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:call me|panggil)\s+(.+)", re.IGNORECASE),
]

# Language style patterns
_STYLE_PATTERNS = [
    (
        re.compile(
            r"(?:pakai|gunakan|pake)\s+(?:bahasa\s+)?(?:formal|resmi)", re.IGNORECASE
        ),
        "formal",
    ),
    (
        re.compile(
            r"(?:pakai|gunakan|pake)\s+(?:bahasa\s+)?(?:santai|kasual|gaul)",
            re.IGNORECASE,
        ),
        "santai",
    ),
    (
        re.compile(
            r"(?:jangan|gak usah|ga usah)\s+(?:terlalu\s+)?(?:formal|kaku)",
            re.IGNORECASE,
        ),
        "santai",
    ),
    (
        re.compile(
            r"(?:jangan|gak usah|ga usah)\s+(?:terlalu\s+)?(?:santai|gaul)",
            re.IGNORECASE,
        ),
        "formal",
    ),
    (
        re.compile(
            r"(?:bicara|ngomong)\s+(?:yang\s+)?(?:formal|resmi|sopan)", re.IGNORECASE
        ),
        "formal",
    ),
    (
        re.compile(r"(?:bicara|ngomong)\s+(?:yang\s+)?(?:santai|biasa)", re.IGNORECASE),
        "santai",
    ),
]

# Output format patterns
_FORMAT_PATTERNS = [
    (
        re.compile(
            r"(?:pakai|tampilkan|format)\s+(?:dalam\s+)?(?:tabel|table)", re.IGNORECASE
        ),
        "tabel",
    ),
    (
        re.compile(
            r"(?:pakai|tampilkan|format)\s+(?:dalam\s+)?(?:narasi|cerita|paragraf)",
            re.IGNORECASE,
        ),
        "narasi",
    ),
    (
        re.compile(
            r"(?:pakai|tampilkan|format)\s+(?:dalam\s+)?(?:list|daftar|bullet)",
            re.IGNORECASE,
        ),
        "list",
    ),
]

# Show preferences patterns
_SHOW_PATTERNS = [
    re.compile(
        r"(?:apa\s+(?:saja\s+)?)?(?:preferensi|setting|pengaturan)\s+(?:saya|aku|gue|gw)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:apa\s+yang\s+(?:kamu|lu|lo)\s+(?:tau|tahu|inget|ingat)\s+tentang\s+(?:saya|aku))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:tampilkan|lihat|show)\s+(?:preferensi|setting)", re.IGNORECASE),
    re.compile(r"(?:my\s+)?prefer[ea]n[cs][ei]", re.IGNORECASE),
]

# Delete preference patterns
_DELETE_PATTERNS = [
    re.compile(r"(?:reset|hapus\s+semua)\s+(?:preferensi|setting)", re.IGNORECASE),
    re.compile(
        r"(?:lupakan|hapus|delete|remove)\s+(?:preferensi|setting)\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(r"jangan\s+panggil\s+(?:saya|aku)\s+(.+?)\s+lagi", re.IGNORECASE),
]


async def detect_preference_intent(
    text: str, pref_mgr: PreferenceManager
) -> Optional[dict]:
    """Detect and handle preference-related intent.

    Returns dict with {handled: True, response: str} if preference intent detected.
    Returns None if not a preference intent.
    """
    text_clean = text.strip()

    # === DELETE preferences (check BEFORE show — "hapus preferensi" contains "preferensi") ===
    for pattern in _DELETE_PATTERNS:
        m = pattern.search(text_clean)
        if m:
            text_lower = text_clean.lower()
            if "semua" in text_lower or (
                "reset" in text_lower and "preferensi" in text_lower
            ):
                return await _handle_delete_all(pref_mgr)
            key_hint = m.group(1) if m.lastindex and m.lastindex >= 1 else None
            if key_hint:
                return await _handle_delete_one(pref_mgr, key_hint.strip())
            return await _handle_show(pref_mgr)

    # === SHOW preferences ===
    for pattern in _SHOW_PATTERNS:
        if pattern.search(text_clean):
            return await _handle_show(pref_mgr)

    # === SET display_name ===
    for pattern in _DISPLAY_NAME_PATTERNS:
        m = pattern.search(text_clean)
        if m:
            name = m.group(1).strip().rstrip(".,!?")
            name = re.sub(
                r"\s+(?:ya|dong|yuk|please)$", "", name, flags=re.IGNORECASE
            ).strip()
            if len(name) < 2 or len(name) > 50:
                continue
            return await _handle_set(pref_mgr, "display_name", name.title())

    # === SET language_style ===
    for pattern, value in _STYLE_PATTERNS:
        if pattern.search(text_clean):
            return await _handle_set(pref_mgr, "language_style", value)

    # === SET output_format ===
    for pattern, value in _FORMAT_PATTERNS:
        if pattern.search(text_clean):
            return await _handle_set(pref_mgr, "output_format", value)

    return None


async def _handle_set(pref_mgr: PreferenceManager, key: str, value: str) -> dict:
    result = await pref_mgr.set_preference(key, value, "explicit_chat")
    label = LABEL_MAP.get(key, key)

    if result["status"] == "ok":
        logger.info(
            "[PREF_DETECT] Set %s=%s for %s/%s",
            key,
            value,
            pref_mgr.tenant_id,
            pref_mgr.user_id,
        )
        msg = _confirmation_message(key, value)
        if result.get("warn_approaching_limit"):
            msg += (
                f" (Kamu sudah punya {result['current_count']} preferensi dari 10 max)"
            )
        return {"handled": True, "response": msg}
    elif result["status"] == "capacity_full":
        return {"handled": True, "response": result["message"]}
    else:
        return {
            "handled": True,
            "response": f"Gagal set {label}: {result.get('message', 'unknown error')}",
        }


def _confirmation_message(key: str, value: str) -> str:
    if key == "display_name":
        return f"Oke, mulai sekarang saya panggil kamu **{value}** ya! \U0001f44b"
    elif key == "language_style":
        style_desc = {"formal": "formal dan sopan", "santai": "santai dan akrab"}
        return f"Baik, saya akan pakai bahasa yang {style_desc.get(value, value)}."
    elif key == "output_format":
        fmt_desc = {
            "tabel": "tabel",
            "narasi": "narasi/paragraf",
            "list": "daftar bullet",
        }
        return f"Oke, default output saya ubah ke format {fmt_desc.get(value, value)}."
    elif key == "language_mix":
        return f"Siap, bahasa diset ke {value}."
    else:
        label = LABEL_MAP.get(key, key)
        return f"Preferensi {label} diset ke {value}."


async def _handle_show(pref_mgr: PreferenceManager) -> dict:
    prefs = await pref_mgr.get_all_preferences()
    if not prefs:
        return {
            "handled": True,
            "response": (
                "Saat ini kamu belum set preferensi apapun. "
                'Kamu bisa bilang "panggil saya Bu Grace" atau '
                '"pakai bahasa santai" untuk memulai.'
            ),
        }
    lines = [f"Saat ini kamu sudah set **{len(prefs)} preferensi**:"]
    for i, p in enumerate(prefs, 1):
        label = LABEL_MAP.get(p["key"], p["key"])
        val = p["value"]
        if isinstance(val, dict):
            val = ", ".join(f"{k}: {v}" for k, v in val.items())
        lines.append(f"{i}. **{label}**: {val}")
    lines.append(
        '\nMau ubah atau hapus? Bilang "hapus preferensi [nama]" atau "reset semua preferensi".'
    )
    return {"handled": True, "response": "\n".join(lines)}


async def _handle_delete_all(pref_mgr: PreferenceManager) -> dict:
    await pref_mgr.delete_all()
    logger.info(
        "[PREF_DETECT] Deleted all prefs for %s/%s",
        pref_mgr.tenant_id,
        pref_mgr.user_id,
    )
    return {
        "handled": True,
        "response": "Semua preferensi sudah di-reset. Kamu bisa set ulang kapan saja.",
    }


async def _handle_delete_one(pref_mgr: PreferenceManager, key_hint: str) -> dict:
    key_hint_lower = key_hint.strip().lower()
    key_map = {
        "panggilan": "display_name",
        "nama": "display_name",
        "bahasa": "language_style",
        "gaya": "language_style",
        "format": "output_format",
    }
    key = key_map.get(key_hint_lower)
    if not key:
        for label, k in LABEL_MAP.items():
            if key_hint_lower in label.lower() or key_hint_lower in k.lower():
                key = k
                break
    if not key:
        return {
            "handled": True,
            "response": f'Preferensi "{key_hint}" tidak ditemukan. Coba "tampilkan preferensi" untuk lihat daftar.',
        }

    result = await pref_mgr.delete_preference(key)
    label = LABEL_MAP.get(key, key)
    if result["status"] == "ok":
        logger.info(
            "[PREF_DETECT] Deleted %s for %s/%s",
            key,
            pref_mgr.tenant_id,
            pref_mgr.user_id,
        )
        return {"handled": True, "response": f"Preferensi **{label}** sudah dihapus."}
    else:
        return {"handled": True, "response": f"Preferensi {label} tidak ditemukan."}
