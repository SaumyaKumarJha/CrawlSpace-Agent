"""Pixel width of a title as Google renders it.

Character counting is the wrong measure and always has been. Google truncates
titles on rendered width, so "Illinois Wills" and "MMMMMMMMMMMMMM" are both 14
characters and only one of them fits. The desktop SERP renders titles in
roughly Arial 20px inside about a 600px container.

Widths below are Arial advance widths in 1/1000 em, which is how font metrics
are published. Accurate to a few pixels — plenty for a threshold check.
"""
from __future__ import annotations

ARIAL_1000 = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556,
    ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556,
    "o": 556, "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556,
    "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584,
    "–": 556, "—": 1000, "|": 260, "·": 278, "•": 350,
    "‘": 222, "’": 222, "“": 333, "”": 333, "…": 1000,
}
DEFAULT_WIDTH = 556          # unknown glyph, roughly a lowercase letter
FONT_SIZE_PX = 20            # Google desktop SERP title
SERP_TITLE_LIMIT_PX = 580    # beyond this it truncates with an ellipsis
SERP_TITLE_MIN_PX = 200      # below this the title is wasting space


def pixel_width(text: str, *, font_size: int = FONT_SIZE_PX) -> int:
    """Approximate rendered width in pixels at the given size."""
    if not text:
        return 0
    total = sum(ARIAL_1000.get(ch, DEFAULT_WIDTH) for ch in text)
    return round(total * font_size / 1000)


def truncates_in_serp(text: str) -> bool:
    return pixel_width(text) > SERP_TITLE_LIMIT_PX
