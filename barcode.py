"""Printable barcodes for Tosslist's special cards (undo, PIN reset).

Code 128 (set B), which every USB barcode scanner reads, drawn as SVG so it
prints sharply at any size. Standard library only.
"""

from html import escape

# Bar/space widths for Code 128 values 0-106 (106 is the stop pattern).
_PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
_START_B = 104
_STOP = 106
QUIET = 10  # blank modules on each side, so scanners can find the edges


def code128_modules(text):
    """The barcode as a list of module widths, alternating bar, space, bar..."""
    if not text or any(not (32 <= ord(c) <= 126) for c in text):
        raise ValueError("Code 128 set B covers printable ASCII only")
    values = [_START_B] + [ord(c) - 32 for c in text]
    check = (values[0] + sum(i * v for i, v in enumerate(values[1:], 1))) % 103
    values += [check, _STOP]
    widths = []
    for v in values:
        widths.extend(int(w) for w in _PATTERNS[v])
    return widths


def svg(text, label=None, module=3, height=110, stretch=False):
    """An SVG image of `text` as a Code 128 barcode, with a caption.
    stretch=True: no caption, and the bars fill whatever box the page gives
    them (a barcode only depends on bar widths, so stretching is safe)."""
    if stretch:
        widths = code128_modules(text)
        total = sum(widths) + 2 * QUIET
        rects, x, is_bar = [], QUIET, True
        for width in widths:
            if is_bar:
                rects.append(f'<rect x="{x}" y="0" width="{width}" height="1"/>')
            x += width
            is_bar = not is_bar
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} 1" '
                f'preserveAspectRatio="none" role="img" aria-label="Barcode: '
                f'{escape(label if label is not None else text)}">'
                f'<rect width="100%" height="100%" fill="#fff"/>'
                f'<g fill="#000">{"".join(rects)}</g></svg>')
    widths = code128_modules(text)
    total = sum(widths) + 2 * QUIET
    w = total * module
    caption = escape(label if label is not None else text)
    bars, x, is_bar = [], QUIET * module, True
    for width in widths:
        if is_bar:
            bars.append(f'<rect x="{x}" y="0" width="{width * module}" height="{height}"/>')
        x += width * module
        is_bar = not is_bar
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{height + 34}" '
            f'viewBox="0 0 {w} {height + 34}" role="img" aria-label="Barcode: {caption}">'
            f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(bars)}</g>'
            f'<text x="{w / 2}" y="{height + 24}" text-anchor="middle" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="18" fill="#000">'
            f'{caption}</text></svg>')
