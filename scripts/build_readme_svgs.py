"""Generate the animated SVGs the README embeds.

    python scripts/build_readme_svgs.py

Animated with SMIL (`<animate>`) rather than a `<style>` block, because GitHub
sanitises SVG when it renders a README image and strips embedded CSS -- SMIL
survives. That constraint is the whole reason these are hand-built rather than
exported from a design tool.

The globe's coastlines are the same Natural Earth data the live page uses, so
the outline is the real thing rather than a drawing of one. Rotation is carried
by the meridians: a meridian on a sphere projects to an ellipse whose semi-minor
axis is r*|cos(angle)|, so animating that axis through a full cycle -- with each
meridian offset in phase -- reads unmistakably as a rotating globe at a fraction
of the size of projecting every coastline at every frame.
"""

from __future__ import annotations

import json
import math
import pathlib

ASSETS = pathlib.Path(__file__).parent.parent / "docs" / "assets"
LAND = pathlib.Path(__file__).parent.parent / "web" / "data" / "land.json"

# Matches web/css/tokens.css. Duplicated deliberately: an SVG cannot read a CSS
# custom property, and GitHub would strip a <style> block that defined them.
BG = "#07070a"
SURFACE = "#0d0d11"
RAISED = "#131318"
BORDER = "#1e1e25"
WHITE = "#ffffff"
TEXT = "#e7e7ec"
DIM = "#9a9aa6"
FAINT = "#63636f"

MONO = "ui-monospace,'SF Mono',Menlo,monospace"
SANS = "Inter,system-ui,-apple-system,'Segoe UI',sans-serif"


def orthographic(lon, lat, yaw, pitch, radius, cx, cy):
    lam = math.radians(lon - yaw)
    phi = math.radians(lat)
    p = math.radians(pitch)
    cos_phi, sin_phi, cos_lam = math.cos(phi), math.sin(phi), math.cos(lam)
    front = math.sin(p) * sin_phi + math.cos(p) * cos_phi * cos_lam
    x = cx + radius * cos_phi * math.sin(lam)
    y = cy - radius * (math.cos(p) * sin_phi - math.sin(p) * cos_phi * cos_lam)
    return x, y, front > 0


def coastline_paths(cx, cy, radius, yaw=22, pitch=14, keep=26):
    """The largest landmasses, projected once and broken at the horizon."""
    rings = json.loads(LAND.read_text())[:keep]
    paths = []

    for ring in rings:
        segments, current = [], []
        for lon, lat in ring:
            x, y, visible = orthographic(lon, lat, yaw, pitch, radius, cx, cy)
            if visible:
                current.append(f"{x:.1f},{y:.1f}")
            elif current:
                segments.append(current)
                current = []
        if current:
            segments.append(current)

        for segment in segments:
            if len(segment) > 2:
                paths.append("M" + "L".join(segment))
    return paths


def meridians(cx, cy, radius, count=6, seconds=14):
    """Ellipses whose semi-minor axis cycles -- the rotation cue."""
    steps = 24
    out = []
    for index in range(count):
        phase = index * (180 / count)
        values = ";".join(
            f"{abs(radius * math.cos(math.radians(phase + 360 * step / steps))):.1f}"
            for step in range(steps + 1)
        )
        out.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="{radius}" ry="{radius}" fill="none" '
            f'stroke="{WHITE}" stroke-opacity=".16" stroke-width="1">'
            f'<animate attributeName="rx" values="{values}" dur="{seconds}s" '
            f'repeatCount="indefinite"/></ellipse>'
        )
    return out


def parallels(cx, cy, radius, count=5):
    out = []
    for index in range(1, count + 1):
        lat = -60 + index * (120 / (count + 1))
        rad = math.radians(lat)
        out.append(
            f'<ellipse cx="{cx}" cy="{cy - radius * math.sin(rad):.1f}" '
            f'rx="{radius * math.cos(rad):.1f}" ry="{radius * math.cos(rad) * 0.18:.1f}" '
            f'fill="none" stroke="{WHITE}" stroke-opacity=".12" stroke-width="1"/>'
        )
    return out


def fade_in(begin, duration=".7s"):
    """One reusable entrance, so the whole frame does not arrive at once."""
    return (
        f'<animate attributeName="opacity" from="0" to="1" begin="{begin}s" '
        f'dur="{duration}" fill="freeze"/>'
    )


def hero_svg() -> str:
    width, height = 1200, 640
    cx, cy, radius = 600, 232, 170

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="Kubera landing page: a rotating wireframe globe above the headline">',
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        # Nav.
        f'<text x="44" y="46" fill="{WHITE}" font-family="{MONO}" font-size="15" '
        f'letter-spacing="5" font-weight="600">KUBERA</text>',
        f'<rect x="1002" y="26" width="154" height="34" rx="17" fill="{WHITE}"/>',
        f'<text x="1079" y="48" fill="{BG}" font-family="{SANS}" font-size="13" '
        f'font-weight="600" text-anchor="middle">Open the desk</text>',
        # Globe.
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{WHITE}" fill-opacity=".03"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{WHITE}" '
        f'stroke-opacity=".3" stroke-width="1"/>',
        *parallels(cx, cy, radius),
        *meridians(cx, cy, radius),
    ]

    coast = "".join(
        f'<path d="{d}" fill="none" stroke="{WHITE}" stroke-opacity=".6" '
        f'stroke-width="1.1" stroke-linejoin="round"/>'
        for d in coastline_paths(cx, cy, radius)
    )
    parts.append(f'<g opacity="0">{coast}{fade_in(0.2, "1.1s")}</g>')

    # Copy.
    parts += [
        f'<g opacity="0">{fade_in(0.5)}'
        f'<text x="{cx}" y="448" fill="{DIM}" font-family="{MONO}" font-size="12" '
        f'letter-spacing="4.4" text-anchor="middle">REAL NSE CLEARING DATA</text></g>',
        # Broken across two lines exactly as the page breaks it. SVG text does
        # not wrap, so the break has to be authored rather than left to the
        # renderer -- one line overflowed the 1200px frame.
        f'<g opacity="0">{fade_in(0.7)}'
        f'<text x="{cx}" y="506" font-family="{SANS}" font-size="44" font-weight="700" '
        f'letter-spacing="-1.3" text-anchor="middle">'
        f'<tspan fill="{DIM}">Your gateway to </tspan>'
        f'<tspan fill="{WHITE}">securities</tspan></text>'
        f'<text x="{cx}" y="556" font-family="{SANS}" font-size="44" font-weight="700" '
        f'letter-spacing="-1.3" text-anchor="middle">'
        f'<tspan fill="{WHITE}">financing</tspan>'
        f'<tspan fill="{DIM}"> analytics</tspan></text></g>',
        f'<g opacity="0">{fade_in(0.95)}'
        f'<rect x="{cx - 176}" y="592" width="150" height="38" rx="19" fill="{WHITE}"/>'
        f'<text x="{cx - 101}" y="616" fill="{BG}" font-family="{SANS}" font-size="14" '
        f'font-weight="600" text-anchor="middle">Open the desk  &#8594;</text>'
        f'<rect x="{cx + 16}" y="592" width="112" height="38" rx="19" fill="none" '
        f'stroke="{BORDER}"/>'
        f'<text x="{cx + 72}" y="616" fill="{TEXT}" font-family="{SANS}" font-size="14" '
        f'text-anchor="middle">API docs</text></g>',
    ]

    parts.append("</svg>")
    return "".join(parts)


def dashboard_svg() -> str:
    """The desk, with the project's real figures."""
    width, height = 1200, 660
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="Kubera dashboard: KPI strip, specialness heatmap and desk attribution">',
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        f'<rect x="0" y="0" width="200" height="{height}" fill="{SURFACE}"/>',
        f'<rect x="199" y="0" width="1" height="{height}" fill="{BORDER}"/>',
        f'<rect x="24" y="26" width="3" height="16" rx="1.5" fill="{WHITE}"/>',
        f'<text x="36" y="40" fill="{WHITE}" font-family="{SANS}" font-size="14" '
        f'font-weight="650">Kubera</text>',
        f'<text x="36" y="58" fill="{FAINT}" font-family="{SANS}" font-size="10">'
        f"SLB · G-Sec repo · FTP</text>",
    ]

    for index, label in enumerate(["Book", "Specialness", "Curves", "Attribution", "Blotter"]):
        y = 100 + index * 28
        parts.append(
            f'<circle cx="30" cy="{y - 4}" r="2" fill="{BORDER}"/>'
            f'<text x="42" y="{y}" fill="{DIM}" font-family="{SANS}" font-size="12">{label}</text>'
        )

    # KPI strip. The lead card carries the accent, as on the page.
    kpis = [
        ("ON LOAN", "₹394.62 cr", "477 positions", False),
        ("NET FINANCING SPREAD", "+65.3 bp", "+₹1.77 L / day", True),
        ("WEIGHTED AVG FEE", "4.77%", "FTP −₹3.21 L / day", False),
        ("UTILISATION", "18.3%", "estimated", False),
    ]
    for index, (label, value, foot, lead) in enumerate(kpis):
        x = 232 + index * 238
        stroke = WHITE if lead else BORDER
        parts.append(
            f'<g opacity="0">{fade_in(0.1 + index * 0.1)}'
            f'<rect x="{x}" y="30" width="216" height="104" rx="12" fill="{RAISED}" '
            f'stroke="{stroke}" stroke-opacity="{0.42 if lead else 1}"/>'
            + (f'<rect x="{x + 1}" y="31" width="214" height="2" fill="{WHITE}"/>' if lead else "")
            + f'<text x="{x + 18}" y="56" fill="{FAINT}" font-family="{SANS}" font-size="9" '
            f'letter-spacing="1.4" font-weight="600">{label}</text>'
            f'<text x="{x + 18}" y="88" fill="{WHITE if lead else TEXT}" '
            f'font-family="{MONO}" font-size="22" font-weight="550">{value}</text>'
            f'<text x="{x + 18}" y="108" fill="{FAINT}" font-family="{SANS}" '
            f'font-size="10">{foot}</text>'
            # The sparkline draws itself in, as on the page.
            f'<path d="M{x + 18},126 L{x + 58},120 L{x + 98},123 L{x + 138},112 '
            f'L{x + 178},116 L{x + 198},108" fill="none" stroke="{WHITE if lead else DIM}" '
            f'stroke-opacity="{1 if lead else 0.5}" stroke-width="1.6" '
            f'stroke-dasharray="260" stroke-dashoffset="260">'
            f'<animate attributeName="stroke-dashoffset" from="260" to="0" '
            f'begin="{0.5 + index * 0.1}s" dur="1.1s" fill="freeze"/></path>'
            f"</g>"
        )

    # Specialness heatmap: the ramp runs dark to white, so the hottest cell is
    # the brightest thing in the panel.
    parts.append(
        f'<g opacity="0">{fade_in(0.9)}'
        f'<rect x="232" y="156" width="560" height="316" rx="12" fill="{SURFACE}" '
        f'stroke="{BORDER}"/>'
        f'<text x="252" y="182" fill="{TEXT}" font-family="{SANS}" font-size="11" '
        f'letter-spacing="1.1" font-weight="650">SPECIALNESS</text>'
        f'<text x="352" y="182" fill="{FAINT}" font-family="{SANS}" font-size="10">'
        f"score by security &amp; tenor bucket</text></g>"
    )

    grid = [
        ("PIIND", [100, None, 41, 58]),
        ("ATHERENERG", [100, None, None, 55]),
        ("LTM", [100, 22, 60, 71]),
        ("GMRAIRPORT", [99, None, None, None]),
        ("STALLION", [49, 99, None, None]),
        ("OFSS", [99, None, 19, 18]),
        ("ETERNAL", [99, None, None, None]),
        ("NYKAA", [99, None, None, None]),
        ("HYUNDAI", [97, 32, 61, 48]),
    ]
    for column, header in enumerate(["0-45d", "46-135d", "136-270d", "271d+"]):
        parts.append(
            f'<text x="{372 + column * 106}" y="204" fill="{FAINT}" font-family="{MONO}" '
            f'font-size="9" text-anchor="middle">{header}</text>'
        )

    for row, (symbol, scores) in enumerate(grid):
        y = 216 + row * 28
        parts.append(
            f'<text x="252" y="{y + 14}" fill="{DIM}" font-family="{MONO}" '
            f'font-size="9">{symbol}</text>'
        )
        for column, score in enumerate(scores):
            x = 372 + column * 106 - 48
            if score is None:
                parts.append(
                    f'<rect x="{x}" y="{y}" width="96" height="20" rx="4" '
                    f'fill="{WHITE}" fill-opacity=".03"/>'
                )
                continue
            shade = 0.12 + (score / 100) ** 1.15 * 0.88
            ink = BG if score >= 50 else TEXT
            parts.append(
                f'<g opacity="0">{fade_in(1.0 + (row * 4 + column) * 0.02, ".5s")}'
                f'<rect x="{x}" y="{y}" width="96" height="20" rx="4" fill="{WHITE}" '
                f'fill-opacity="{shade:.2f}"/>'
                f'<text x="{x + 48}" y="{y + 14}" fill="{ink}" font-family="{MONO}" '
                f'font-size="10" font-weight="600" text-anchor="middle">{score}</text></g>'
            )

    # The FTP result -- the project's headline finding.
    parts.append(
        f'<g opacity="0">{fade_in(1.15)}'
        f'<rect x="808" y="156" width="368" height="316" rx="12" fill="{SURFACE}" '
        f'stroke="{BORDER}"/>'
        f'<text x="828" y="182" fill="{TEXT}" font-family="{SANS}" font-size="11" '
        f'letter-spacing="1.1" font-weight="650">DESK &amp; TREASURY SPLIT</text>'
        f'<text x="828" y="200" fill="{FAINT}" font-family="{SANS}" font-size="9.5">'
        f"a matched book pays no FTP</text></g>"
    )

    rows = [
        ("EQ_FIN", "₹789 cr", "₹0", "+₹1.31 L", "₹0"),
        ("DELTA_ONE", "₹201 cr", "₹201 cr", "−₹2.73 L", "−₹3.21 L"),
        ("TREASURY", "₹0", "−₹201 cr", "₹0", "+₹3.21 L"),
    ]
    columns_x = [828, 952, 1026, 1100, 1168]
    for column, header in enumerate(["DESK", "GROSS", "NET", "FEE", "FTP"]):
        anchor = "start" if column == 0 else "end"
        x = columns_x[column]
        parts.append(
            f'<text x="{x}" y="230" fill="{FAINT}" font-family="{SANS}" font-size="8.5" '
            f'letter-spacing="1" font-weight="600" text-anchor="{anchor}">{header}</text>'
        )

    for row, cells in enumerate(rows):
        y = 256 + row * 30
        parts.append(f'<g opacity="0">{fade_in(1.25 + row * 0.12)}')
        for column, cell in enumerate(cells):
            anchor = "start" if column == 0 else "end"
            x = columns_x[column]
            fill = TEXT if column == 0 else (FAINT if cell in {"₹0"} else TEXT)
            parts.append(
                f'<text x="{x}" y="{y}" fill="{fill}" font-family="{MONO}" '
                f'font-size="10.5" text-anchor="{anchor}">{cell}</text>'
            )
        parts.append(f'<rect x="828" y="{y + 9}" width="328" height="1" fill="{BORDER}"/></g>')

    parts.append(
        f'<g opacity="0">{fade_in(1.7)}'
        f'<text x="828" y="392" fill="{DIM}" font-family="{SANS}" font-size="10.5">'
        f"Delta One pays MORE in internal funding</text>"
        f'<text x="828" y="410" fill="{DIM}" font-family="{SANS}" font-size="10.5">'
        f"than it pays in borrow fees.</text>"
        f'<text x="828" y="440" fill="{WHITE}" font-family="{MONO}" font-size="11">'
        f"−₹3.21 L  vs  −₹2.73 L</text></g>"
    )

    # Blotter strip.
    parts.append(
        f'<g opacity="0">{fade_in(1.45)}'
        f'<rect x="232" y="492" width="944" height="140" rx="12" fill="{SURFACE}" '
        f'stroke="{BORDER}"/>'
        f'<text x="252" y="518" fill="{TEXT}" font-family="{SANS}" font-size="11" '
        f'letter-spacing="1.1" font-weight="650">BLOTTER</text></g>'
    )

    blotter = [
        ("RVNL", "XN", "LEND", "1,99,674", "₹4.28 cr", "47d", "0.83%", "49"),
        ("WAAREEENER", "XO", "BORROW", "15,843", "₹4.06 cr", "19d", "6.53%", "70"),
        ("BLUESTARCO", "XO", "LEND", "25,589", "₹3.92 cr", "25d", "14.13%", "67"),
    ]
    for row, cells in enumerate(blotter):
        y = 556 + row * 24
        parts.append(f'<g opacity="0">{fade_in(1.55 + row * 0.1)}')
        for column, cell in enumerate(cells):
            anchor = "start" if column < 3 else "end"
            x = [252, 352, 412, 620, 740, 820, 920, 1000][column]
            fill = TEXT
            if column == 1:
                fill = FAINT
            parts.append(
                f'<text x="{x}" y="{y}" fill="{fill}" font-family="{MONO}" '
                f'font-size="10" text-anchor="{anchor}">{cell}</text>'
            )
        parts.append("</g>")

    parts.append("</svg>")
    return "".join(parts)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, svg in (("hero.svg", hero_svg()), ("dashboard.svg", dashboard_svg())):
        path = ASSETS / name
        path.write_text(svg)
        print(f"{path.relative_to(path.parents[2])}  {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
