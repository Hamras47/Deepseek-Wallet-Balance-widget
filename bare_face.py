"""The balance, drawn with no background (see bare.py): badge, amount, footer.

Sizes and positions are the card's own (styles.css, narrow-card layout), so switching
between the card and this face changes the background and nothing else.
"""

from __future__ import annotations

from bare import Drawing, Sketch

WIDTH, HEIGHT = 200.0, 98.0

BADGES = {
    "ok": "BALANCE", "low": "OUT OF CREDIT", "empty": "NO CREDIT", "stale": "LAST KNOWN",
    "offline": "OFFLINE", "no_key": "NO API KEY", "auth": "KEY REJECTED", "error": "PROBLEM",
}
COLOURS = {
    "ok": (82, 221, 143), "low": (224, 118, 127), "empty": (224, 118, 127),
    "auth": (224, 118, 127), "error": (224, 118, 127),
    "stale": (232, 190, 108), "offline": (232, 190, 108), "no_key": (150, 170, 190),
}


def render(payload: dict, ink: str, scale: float) -> Drawing:
    s = Sketch(WIDTH, HEIGHT, scale, ink)
    state = str(payload.get("state", "ok"))
    colour = COLOURS.get(state, COLOURS["no_key"])
    s.pulse_dot(13, 17, 3, colour)
    s.text(22, 20.6, BADGES.get(state, state.upper()), 10, "display", rgb=colour, spacing=1.2)
    amount = str(payload.get("amount") or "--")
    width = s.text(10, 58, amount, 28, "display")
    if payload.get("balance") is not None:
        s.text(10 + width + 7, 58, str(payload.get("currency", "")), 11, alpha=215)
    foot = "refreshing\u2026" if payload.get("refreshing") else str(payload.get("foot", ""))
    s.text(10, 88, s.fit(foot, 11.5, "text", WIDTH - 20), 11.5, alpha=215)
    return s.finish()
