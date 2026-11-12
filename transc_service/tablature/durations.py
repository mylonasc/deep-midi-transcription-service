"""Exact AlphaTex duration pieces at 480 PPQ.

Adapted from Open-Fret render.py at 3a62b4c (MIT), copyright 2026 Open-Fret
contributors. See THIRD_PARTY_NOTICES.md. No model/runtime dependency.
"""
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class DurationPiece:
    ticks: int
    denominator: int
    dots: int = 0
    tuplet: int = 1

    def effects(self):
        values = (["d" * self.dots] if self.dots else [])
        if self.tuplet != 1:
            values.append(f"tu {self.tuplet}")
        return "{" + " ".join(values) + "}" if values else ""


def _specs():
    result = {}
    for denominator in (1, 2, 4, 8, 16, 32, 64, 128, 256):
        base = 3840 // denominator  # AlphaTab native 960 PPQ integer arithmetic
        for dots in range(3):
            dotted = base if dots == 0 else base + (base // 2 if dots == 1 else (base // 4) * 3)
            for tuplet, ratio in {1: 1, 3: 2, 5: 4, 6: 4, 7: 4, 9: 8, 10: 8, 11: 8, 12: 8}.items():
                native = dotted * ratio // tuplet
                if native % 2 or native <= 0:
                    continue
                piece = DurationPiece(native // 2, denominator, dots, tuplet)
                old = result.get(piece.ticks)
                def rank(p):
                    return p.tuplet != 1, p.dots, p.denominator, p.tuplet
                if old is None or rank(piece) < rank(old):
                    result[piece.ticks] = piece
    return result


SPECS = _specs()


@lru_cache(maxsize=4096)
def duration_pieces(ticks: int) -> tuple[DurationPiece, ...]:
    if ticks <= 0 or ticks > 1_000_000:
        raise ValueError(f"Unsupported notation duration: {ticks} ticks")
    if ticks in SPECS:
        return (SPECS[ticks],)
    if ticks > 3840:
        count = (ticks - 1920) // 1920
        remainder = ticks - count * 1920
        return (SPECS[1920],) * count + duration_pieces(remainder)
    counts, previous = [0] + [ticks + 1] * ticks, [None] * (ticks + 1)
    for value in range(1, ticks + 1):
        for coin in sorted(SPECS, reverse=True):
            if coin <= value and counts[value - coin] + 1 < counts[value]:
                counts[value], previous[value] = counts[value - coin] + 1, coin
    if previous[ticks] is None:
        raise ValueError(f"Cannot represent exactly {ticks} ticks in AlphaTex")
    output = []
    while ticks:
        coin = previous[ticks]
        output.append(SPECS[coin])
        ticks -= coin
    return tuple(output)
