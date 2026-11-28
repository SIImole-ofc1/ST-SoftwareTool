"""Settings › Theme › Performance mode: for older or busy computers.

On: no animations (sections, lists, the music equalizer) and live numbers are refreshed half
as often (performance graphs and widgets, music, the menu-bar popup). Off: everything as usual,
animations following Settings › Theme › Animations.
"""
from __future__ import annotations

ON = False


def interval(normal_ms: int) -> int:
    """How often to refresh something that normally refreshes every `normal_ms`."""
    return normal_ms * 2 if ON else normal_ms


def animations_on(settings: dict) -> bool:
    return bool(settings.get('animations', True)) and not ON


def load(settings: dict) -> None:
    global ON
    ON = bool(settings.get('performance_mode', False))
