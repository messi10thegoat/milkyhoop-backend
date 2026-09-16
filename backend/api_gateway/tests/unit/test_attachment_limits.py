"""Gerbang Unit B: satu sumber batas lampiran. 9MB diterima, 11MB & tipe tak sah ditolak."""
import pytest
from fastapi import HTTPException

from app.attachment_limits import (
    ATTACHMENT_ALLOWED_TYPES,
    ATTACHMENT_MAX_BYTES,
    ATTACHMENT_MAX_MB,
    enforce_attachment_limits,
)

MB = 1024 * 1024


def test_b_limit_is_10mb():
    assert ATTACHMENT_MAX_MB == 10
    assert ATTACHMENT_MAX_BYTES == 10 * MB


def test_b_14_types_reference():
    assert len(ATTACHMENT_ALLOWED_TYPES) == 14
    assert "application/pdf" in ATTACHMENT_ALLOWED_TYPES
    assert "image/jpeg" in ATTACHMENT_ALLOWED_TYPES


def test_b_9mb_accepted():
    enforce_attachment_limits(9 * MB, "application/pdf")  # tak meledak
    enforce_attachment_limits(9 * MB, "image/jpeg")


def test_b_11mb_rejected():
    with pytest.raises(HTTPException) as ei:
        enforce_attachment_limits(11 * MB, "application/pdf")
    assert ei.value.status_code == 400
    assert "10 MB" in ei.value.detail


def test_b_bad_type_rejected():
    with pytest.raises(HTTPException) as ei:
        enforce_attachment_limits(1024, "application/x-msdownload")
    assert ei.value.status_code == 400
    assert "tidak didukung" in ei.value.detail
