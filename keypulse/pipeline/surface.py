"""Compat shim: the surface snapshot now lives in core (keypulse.hud.surface)."""
from keypulse.hud.surface import *  # noqa: F401,F403
from keypulse.hud.surface import MIN_SCORE, build_surface_snapshot, translate_why_selected  # noqa: F401
