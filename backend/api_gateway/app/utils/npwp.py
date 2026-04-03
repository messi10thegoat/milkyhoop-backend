"""NPWP 15→16 digit handling for Coretax compliance."""
import re


def normalize_npwp(npwp: str) -> str:
    """Strip non-digit characters."""
    if not npwp:
        return ""
    return re.sub(r"[^0-9]", "", npwp)


def npwp_to_16(npwp: str) -> str:
    """Convert 15-digit NPWP to 16-digit by prepending 0."""
    clean = normalize_npwp(npwp)
    if len(clean) == 15:
        return "0" + clean
    return clean


def validate_npwp(npwp: str) -> dict:
    """Validate NPWP and return status dict."""
    clean = normalize_npwp(npwp)
    if not clean:
        return {"valid": False, "message": "NPWP kosong"}
    if len(clean) == 16:
        return {"valid": True, "digits": 16, "warning": None}
    if len(clean) == 15:
        return {
            "valid": True,
            "digits": 15,
            "warning": "NPWP 15 digit. Mulai 2026 wajib 16 digit.",
            "suggested_16": "0" + clean,
        }
    return {
        "valid": False,
        "message": f"NPWP harus 15 atau 16 digit (ditemukan {len(clean)} digit)",
    }
