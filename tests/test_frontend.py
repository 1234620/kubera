"""Stage 8 check: the dashboard's own rules, enforced.

Most of this is a linter for rules/FRONTEND.md -- the rules are only worth
writing down if something checks them. No browser needed.
"""

import pathlib
import re

import pytest

WEB = pathlib.Path(__file__).parents[1] / "web"
INDEX = WEB / "index.html"
TOKENS = WEB / "css" / "tokens.css"

SOURCES = sorted(WEB.rglob("*.html")) + sorted(WEB.rglob("*.css")) + sorted(WEB.rglob("*.js"))


def test_the_dashboard_exists():
    assert INDEX.is_file()
    assert "SLB &amp; Repo Financing Desk" in INDEX.read_text()


def test_there_is_no_build_step():
    """ADR 0003: static files only. A package.json would mean a toolchain."""
    assert not (WEB / "package.json").exists()
    assert not (WEB / "node_modules").exists()
    assert not list(WEB.rglob("*.jsx"))
    assert not list(WEB.rglob("*.ts"))


def test_there_is_no_login_anywhere():
    """rules/FRONTEND.md: no auth page, ever. This runs on localhost."""
    banned = re.compile(r"\b(login|signin|sign-in|password|logout|jwt|bearer)\b", re.I)
    offenders = [path.name for path in SOURCES if banned.search(path.read_text())]
    assert not offenders


@pytest.mark.parametrize(
    "path",
    [p for p in SOURCES if p.name != "tokens.css"],
    ids=lambda p: p.name,
)
def test_no_colour_literals_outside_tokens(path):
    """Every colour is a custom property defined in tokens.css and nowhere else.

    Charts read colours through getComputedStyle for the same reason, so a theme
    change reaches the canvas too.
    """
    text = path.read_text()
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", text)
    # rgb(0 0 0 / x) shadows are neutral opacity, not palette, and are allowed.
    assert not hexes, hexes


def test_tokens_define_both_themes():
    text = TOKENS.read_text()
    assert ":root" in text
    assert "prefers-color-scheme: light" in text
    # Dark is the default because this is a trading screen.
    assert "color-scheme: dark" in text


def test_the_specialness_ramp_is_oklch():
    """A naive HSL ramp bunches up in the greens and misleads the eye."""
    text = TOKENS.read_text()
    assert text.count("oklch(") >= 5
    for step in range(5):
        assert f"--spec-{step}:" in text


def test_the_palette_names_its_reference():
    """A design should be able to say where it came from.

    tokens.css cites the Robinhood identity by Porto Rocha and carries its Robin
    Neon value, rather than presenting colours nobody can account for.
    """
    text = TOKENS.read_text()
    assert "PORTO ROCHA" in text
    assert "#CCFF00" in text or "#ccff00" in text


def test_there_is_only_one_accent():
    """Restraint is the whole lesson from the references.

    The first version had --accent, --accent-2 and --accent-3 and used all three
    at once, so six KPI cards competed instead of ranking.
    """
    text = TOKENS.read_text()
    assert "--accent-2:" not in text
    assert "--accent-3:" not in text


def test_exactly_one_kpi_card_is_the_lead():
    """Counts the declaration, not the prose: the comment above the list also
    contains the words, which is why this matches the trailing comma."""
    app = (WEB / "js" / "app.js").read_text()
    assert app.count("lead: true,") == 1


def test_reduced_motion_is_honoured():
    """An accessibility floor, not an optional extra (rules/FRONTEND.md)."""
    css = (WEB / "css" / "app.css").read_text()
    assert "prefers-reduced-motion: reduce" in css

    # And the count-up skips outright rather than animating fast.
    assert "prefers-reduced-motion" in (WEB / "js" / "format.js").read_text()
    assert "prefers-reduced-motion" in (WEB / "js" / "charts.js").read_text()


def test_the_motion_the_rules_require_is_present():
    """`drift` is deliberately absent.

    The first design had three coloured blobs drifting behind every card. That is
    exactly the "fintech rainbow gradient" the Robinhood/Porto Rocha rebrand won
    its category for rejecting, so the background is now a single static warm
    lift and the motion is all purposeful: entry, draw-in, shimmer, alert.
    """
    css = (WEB / "css" / "app.css").read_text()
    for keyframe in ("rise", "rowin", "fade", "shimmer", "called", "beat", "draw"):
        assert f"@keyframes {keyframe}" in css, keyframe
    assert "@keyframes drift" not in css

    # Gradients still do work -- surface depth, the rail, the specialness ramp,
    # the skeleton sweep -- they just no longer shout.
    assert css.count("linear-gradient") >= 6
    assert css.count("radial-gradient") >= 2


def test_only_allowed_cdns_are_referenced():
    """rules/CODING.md keeps the dependency list to one screen."""
    urls = re.findall(r"https://([a-z0-9.\-]+)/", INDEX.read_text())
    assert set(urls) <= {"cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"}


def test_money_is_formatted_for_the_indian_market():
    """Lakh and crore, because that is how the market these numbers come from reads."""
    fmt = (WEB / "js" / "format.js").read_text()
    assert 'Intl.NumberFormat("en-IN"' in fmt
    assert "CRORE" in fmt and "LAKH" in fmt


def test_the_frontend_does_not_compute_analytics():
    """It formats and draws. Anything beyond a percentage belongs in SQL.

    The giveaway would be arithmetic over a fee and a tenor in the browser, so
    this checks the annualisation formula does not appear client-side.
    """
    app = (WEB / "js" / "app.js").read_text()
    assert "365" not in app
    assert "fee_annualised_pct" in app  # consumed, not derived


def test_no_duplicate_element_ids():
    """A duplicate id is a silent, ugly bug.

    The blotter section briefly shared id="blotter" with its own tbody, so
    setting innerHTML hit the SECTION first and wiped the header controls the
    next line then tried to write into. Section anchors carry a `sec-` prefix
    precisely so they cannot collide with the ids the JS writes into.
    """
    ids = re.findall(r'\bid="([^"]+)"', INDEX.read_text())
    duplicates = {value for value in ids if ids.count(value) > 1}
    assert not duplicates, duplicates


def test_every_nav_anchor_resolves():
    html = INDEX.read_text()
    targets = set(re.findall(r'\bid="([^"]+)"', html))
    anchors = {a for a in re.findall(r'href="#([^"]+)"', html)}
    assert anchors <= targets, anchors - targets


def test_numbers_are_tabular():
    """Columns of digits have to line up to be scannable."""
    css = (WEB / "css" / "app.css").read_text()
    assert "font-variant-numeric: tabular-nums" in css
