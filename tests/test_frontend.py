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


def test_reduced_motion_is_honoured():
    """An accessibility floor, not an optional extra (rules/FRONTEND.md)."""
    css = (WEB / "css" / "app.css").read_text()
    assert "prefers-reduced-motion: reduce" in css

    # And the count-up skips outright rather than animating fast.
    assert "prefers-reduced-motion" in (WEB / "js" / "format.js").read_text()
    assert "prefers-reduced-motion" in (WEB / "js" / "charts.js").read_text()


def test_the_motion_the_rules_require_is_present():
    css = (WEB / "css" / "app.css").read_text()
    for keyframe in ("drift", "rise", "rowin", "shimmer", "called", "draw"):
        assert f"@keyframes {keyframe}" in css, keyframe
    # Gradients on the background, the cards and the panels.
    assert css.count("linear-gradient") >= 6
    assert css.count("radial-gradient") >= 3


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


def test_numbers_are_tabular():
    """Columns of digits have to line up to be scannable."""
    css = (WEB / "css" / "app.css").read_text()
    assert "font-variant-numeric: tabular-nums" in css
