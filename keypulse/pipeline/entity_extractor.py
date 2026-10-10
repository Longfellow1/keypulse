"""Compat shim: the rule-based entity extractor now lives in core (keypulse.sources)."""
from keypulse.sources.entity_extractor import *  # noqa: F401,F403
from keypulse.sources.entity_extractor import (  # noqa: F401
    Entity,
    extract,
    extract_keywords,
    extract_text_keywords,
)
