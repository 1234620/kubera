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
    assert "Kubera" in INDEX.read_text()


def test_there_is_no_build_step():
    """ADR 0003: static files only. A package.json would mean a toolchain."""
    assert not (WEB / "package.json").exists()
    assert not (WEB / "node_modules").exists()
    assert not list(WEB.rglob("*.jsx"))
    assert not list(WEB.rglob("*.ts"))


def test_the_globe_reads_its_colours_from_tokens():
    """Canvas cannot read a CSS gradient, so it is the easiest place to smuggle a
    hardcoded colour back in. It reads the tokens instead."""
    globe = (WEB / "js" / "globe.js").read_text()
    assert 'token("--accent")' in globe
    assert 'token("--globe-core")' in globe

    tokens = TOKENS.read_text()
    for name in ("--globe-core", "--globe-mid", "--globe-edge"):
        assert f"{name}:" in tokens, name


def test_the_globe_data_is_local_and_attributed():
    """No runtime dependency on a third-party tile server or texture host."""
    land = WEB / "data" / "land.json"
    assert land.is_file()
    assert land.stat().st_size < 200_000

    globe = (WEB / "js" / "globe.js").read_text()
    assert "Natural Earth" in globe
    assert "public" in globe.lower()
    assert "http" not in globe  # the fetch is a relative path


def test_the_globe_is_reachable_without_a_pointer():
    """A drag-only object is unusable by keyboard, and it is the page's one
    interactive ornament."""
    globe = (WEB / "js" / "globe.js").read_text()
    assert "keydown" in globe
    assert "ArrowLeft" in globe
    assert 'tabindex="0"' in INDEX.read_text()


def test_the_hero_leads_with_a_verifiable_claim():
    """The landing page's numbers are the ones the tests actually assert, not
    marketing figures nobody can check."""
    html = INDEX.read_text()
    assert "132 / 133" in html
    assert "0.000 bp" in html
    assert "synthetic" in html  # the book's status is stated, not hidden


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

    tokens.css cites the Awwwards "Algorithmic Trading Dashboard" concept the
    brief supplied as the target, and records that the brief asked for black and
    white where the reference used black and blue -- which is the decision that
    shaped the whole palette.
    """
    text = TOKENS.read_text()
    assert "Awwwards" in text
    assert "BLACK + WHITE" in text


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
    for keyframe in ("rise", "rowin", "fade", "shimmer", "called", "beat", "breathe", "draw"):
        assert f"@keyframes {keyframe}" in css, keyframe
    assert "@keyframes drift" not in css

    # Gradients still do work -- surface depth, the rail, the specialness ramp,
    # the skeleton sweep, the feather behind the hero copy -- they just no longer
    # shout. Only one radial now: the drifting blobs are gone and the hero relies
    # on the globe instead.
    assert css.count("linear-gradient") >= 6
    assert css.count("radial-gradient") >= 1


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


def test_the_hero_cannot_blow_out_its_container():
    """The hero's child must be width-constrained, whichever layout it uses.

    This bit once and was nearly invisible. Both grid and flex size a track or
    item to its CONTENT by default, and the hero's stats grid wants four 118px
    columns -- which resolved to 598px inside a 375px viewport. `overflow:
    hidden` then clipped the headline with no scrollbar, no console error, and
    `scrollWidth` reporting no overflow at all.

    The guard differs by layout, so both forms are accepted:
      grid -> grid-template-columns: minmax(0, 1fr)
      flex -> min-width: 0 on the child
    """
    css = (WEB / "css" / "app.css").read_text()

    hero = css[css.index(".hero {") :]
    hero = hero[: hero.index("}")]
    hero_copy = css[css.index(".hero-copy {") :]
    hero_copy = hero_copy[: hero_copy.index("}")]

    grid_guard = "grid-template-columns: minmax(0, 1fr)" in hero
    flex_guard = "flex-direction: column" in hero and "min-width: 0" in hero_copy
    assert grid_guard or flex_guard, "hero has no width guard"

    assert "width: 100%" in hero_copy


def test_numbers_are_tabular():
    """Columns of digits have to line up to be scannable."""
    css = (WEB / "css" / "app.css").read_text()
    assert "font-variant-numeric: tabular-nums" in css
