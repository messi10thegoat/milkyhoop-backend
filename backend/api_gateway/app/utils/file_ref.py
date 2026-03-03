"""
Utility functions for file reference resolution.
Opaque file references hide server filesystem paths from the LLM context.
"""
import os
import re
from typing import Optional

UPLOAD_BASE_DIR = "/tmp/milkyhoop_uploads"


def resolve_file_ref(file_ref: str, tenant_id: str) -> Optional[str]:
    """
    Resolve an opaque file reference to an actual filesystem path.
    Format: 'chat_upload:<hash><ext>' -> '/tmp/milkyhoop_uploads/<tenant>/chat/<hash><ext>'
    Returns None if file_ref is invalid or file doesn't exist.
    """
    if not file_ref or ':' not in file_ref:
        return None
    prefix, hash_ext = file_ref.split(':', 1)
    if prefix != 'chat_upload' or not hash_ext:
        return None
    # Sanitize: only allow hex chars + dot + extension
    if not re.match(r'^[a-f0-9]+\.[a-z0-9]+$', hash_ext):
        return None
    path = os.path.join(UPLOAD_BASE_DIR, tenant_id, 'chat', hash_ext)
    return path if os.path.exists(path) else None
