"""The Living Bar Chronicle — persona handling and noir vignette generation."""

from .chronicle import Chronicle, ChronicleEvent
from .engine import VignetteResult, generate_vignette
from .persistence import CachedChronicle, chronicle_cache_key
from .personas import random_persona
from .safety import PersonaRejected, guard_vignette, sanitize_persona

__all__ = [
    "CachedChronicle",
    "Chronicle",
    "ChronicleEvent",
    "PersonaRejected",
    "VignetteResult",
    "chronicle_cache_key",
    "generate_vignette",
    "guard_vignette",
    "random_persona",
    "sanitize_persona",
]
