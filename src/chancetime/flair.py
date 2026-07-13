"""Mario Party-inspired slogans for logs and alerts.

The risk engine is not random. The flavor text is.
Display name: Chance Time. CLI/package: chancetime.
"""

from __future__ import annotations

# Product
DISPLAY_NAME = "Chance Time"
PACKAGE_NAME = "chancetime"

# Mini-game callouts (1-player item minigame energy)
GOT_ITEM = "got item"
MISS = "miss"

# Optional later: start-of-loop / halt flair
CHANCE_TIME = "chance time"
ITEM_BAG = "item bag"


def fill_slogan(*, paper: bool = True) -> str:
    """Slogan for a successful fill (paper or live)."""
    base = GOT_ITEM
    return f"{base} (paper)" if paper else base


def miss_slogan(*, reason: str = "") -> str:
    """Slogan for a rejected / failed order path."""
    if reason:
        return f"{MISS} ({reason})"
    return MISS
