"""Spinner verbs for the busy/thinking status line.

Mirrors ``typescript/src/constants/spinnerVerbs.ts``. The status line shows
one of these gerunds while the agent is working (e.g. ``Cogitating…``,
``Frolicking…``). TS picks a fresh verb per spinner mount via
``sample(getSpinnerVerbs())``; the Python port samples one per thinking
session.

The list is kept byte-identical to the TS source (including ``Beboppin'``
and the accented ``Flambéing`` / ``Sautéing``) so the two implementations
draw from the same pool.
"""
from __future__ import annotations

import random
from typing import Final

# Verbatim from typescript/src/constants/spinnerVerbs.ts:16-204 (187 entries).
SPINNER_VERBS: Final[tuple[str, ...]] = (
    "Accomplishing", "Actioning", "Actualizing", "Architecting",
    "Baking", "Beaming", "Beboppin'", "Befuddling",
    "Billowing", "Blanching", "Bloviating", "Boogieing",
    "Boondoggling", "Booping", "Bootstrapping", "Brewing",
    "Bunning", "Burrowing", "Calculating", "Canoodling",
    "Caramelizing", "Cascading", "Catapulting", "Cerebrating",
    "Channeling", "Channelling", "Choreographing", "Churning",
    "Clauding", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering",
    "Contemplating", "Cooking", "Crafting", "Creating",
    "Crunching", "Crystallizing", "Cultivating", "Deciphering",
    "Deliberating", "Determining", "Dilly-dallying", "Discombobulating",
    "Doing", "Doodling", "Drizzling", "Ebbing",
    "Effecting", "Elucidating", "Embellishing", "Enchanting",
    "Envisioning", "Evaporating", "Fermenting", "Fiddle-faddling",
    "Finagling", "Flambéing", "Flibbertigibbeting", "Flowing",
    "Flummoxing", "Fluttering", "Forging", "Forming",
    "Frolicking", "Frosting", "Gallivanting", "Galloping",
    "Garnishing", "Generating", "Gesticulating", "Germinating",
    "Gitifying", "Grooving", "Gusting", "Harmonizing",
    "Hashing", "Hatching", "Herding", "Honking",
    "Hullaballooing", "Hyperspacing", "Ideating", "Imagining",
    "Improvising", "Incubating", "Inferring", "Infusing",
    "Ionizing", "Jitterbugging", "Julienning", "Kneading",
    "Leavening", "Levitating", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Misting",
    "Moonwalking", "Moseying", "Mulling", "Mustering",
    "Musing", "Nebulizing", "Nesting", "Newspapering",
    "Noodling", "Nucleating", "Orbiting", "Orchestrating",
    "Osmosing", "Perambulating", "Percolating", "Perusing",
    "Philosophising", "Photosynthesizing", "Pollinating", "Pondering",
    "Pontificating", "Pouncing", "Precipitating", "Prestidigitating",
    "Processing", "Proofing", "Propagating", "Puttering",
    "Puzzling", "Quantumizing", "Razzle-dazzling", "Razzmatazzing",
    "Recombobulating", "Reticulating", "Roosting", "Ruminating",
    "Sautéing", "Scampering", "Schlepping", "Scurrying",
    "Seasoning", "Shenaniganing", "Shimmying", "Simmering",
    "Skedaddling", "Sketching", "Slithering", "Smooshing",
    "Sock-hopping", "Spelunking", "Spinning", "Sprouting",
    "Stewing", "Sublimating", "Swirling", "Swooping",
    "Symbioting", "Synthesizing", "Tempering", "Thinking",
    "Thundering", "Tinkering", "Tomfoolering", "Topsy-turvying",
    "Transfiguring", "Transmuting", "Twisting", "Undulating",
    "Unfurling", "Unravelling", "Vibing", "Waddling",
    "Wandering", "Warping", "Whatchamacalliting", "Whirlpooling",
    "Whirring", "Whisking", "Wibbling", "Working",
    "Wrangling", "Zesting", "Zigzagging",
)

# TS fallback when the pool is somehow empty (Spinner.tsx:449).
_FALLBACK_VERB: Final[str] = "Working"


def get_spinner_verbs() -> list[str]:
    """Return the active spinner-verb pool, honoring the user setting.

    Mirrors TS ``getSpinnerVerbs`` (spinnerVerbs.ts:3-13):

    * no ``spinner_verbs`` setting → the built-in defaults;
    * ``mode="replace"`` → the user's verbs (or defaults if the user list
      is empty — TS guards the empty-replace case the same way);
    * ``mode="append"`` → defaults followed by the user's verbs.

    Settings are read lazily so this module stays import-light and the
    TUI never has to be on the path.
    """
    try:
        from src.settings.settings import get_settings

        config = get_settings().spinner_verbs
    except Exception:
        config = None

    if config is None:
        return list(SPINNER_VERBS)

    # Guard a malformed setting (e.g. ``verbs: "Vibing"`` as a bare string):
    # iterate-as-list would char-explode it into single letters. Treat any
    # non-list as "no custom verbs" rather than producing junk verbs.
    user_verbs = list(config.verbs) if isinstance(config.verbs, list) else []
    if config.mode == "replace":
        return user_verbs if user_verbs else list(SPINNER_VERBS)
    # "append" (and any unexpected mode) → defaults + user verbs.
    return list(SPINNER_VERBS) + user_verbs


def pick_spinner_verb() -> str:
    """Pick a random spinner verb from the active pool.

    Single indirection point for randomness so tests can monkeypatch it
    deterministically. Falls back to ``"Working"`` if the pool is empty
    (matches TS ``sample(getSpinnerVerbs()) ?? "Working"``).
    """
    verbs = get_spinner_verbs()
    if not verbs:
        return _FALLBACK_VERB
    return random.choice(verbs)


__all__ = ["SPINNER_VERBS", "get_spinner_verbs", "pick_spinner_verb"]
