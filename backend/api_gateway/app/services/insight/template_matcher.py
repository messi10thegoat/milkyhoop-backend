import logging
import re
from .query_templates import QUERY_TEMPLATES

logger = logging.getLogger(__name__)


class TemplateMatcher:
    """
    Match user text to a query template.
    Keyword-based, deterministic, no LLM.
    """

    def match(self, text: str):
        """
        Returns (template_id, params) or (None, {}).
        Picks the longest matching pattern (most specific).
        """
        text_lower = text.lower().strip()
        best_match = None
        best_score = 0

        for template_id, template in QUERY_TEMPLATES.items():
            for pattern in template["patterns"]:
                if pattern in text_lower:
                    score = len(pattern)
                    if score > best_score:
                        best_match = template_id
                        best_score = score

        if not best_match:
            return None, {}

        params = self._extract_params(text, best_match)
        logger.debug(f"TemplateMatcher: '%s' -> %s, params=%s", text, best_match, params)
        return best_match, params

    def _extract_params(self, text, template_id):
        """Extract dynamic params (e.g., search query) from user text."""
        params = {}

        if "SEARCH" in template_id:
            template = QUERY_TEMPLATES[template_id]
            text_lower = text.lower()
            for pattern in sorted(template["patterns"], key=len, reverse=True):
                if pattern in text_lower:
                    idx = text_lower.index(pattern)
                    remainder = text[idx + len(pattern):].strip()
                    # Clean up common suffixes
                    remainder = re.sub(r"\?$", "", remainder).strip()
                    if remainder:
                        params["search"] = remainder
                    break

        return params
