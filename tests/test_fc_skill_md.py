"""Drift tests tying skills/frontier-commits/SKILL.md to the code it documents.

SKILL.md is the PRODUCTION path — the scheduled weekly run is a Claude
invocation following its "Unattended weekly run" and "Script template"
sections, so a shape or story type that exists only in fc_script_plan /
fc_stories never reaches a real episode, and a table transcription slip ships
a different rotation than the one the property tests verified. Same
discipline as the daily show's test_skill_md_* suite in test_reliability.py.

Bank tables are CELL-parsed, never substring-matched: a bank name that merely
appears somewhere in the prose must not satisfy the check — deleting a table
row or renaming a cell has to go red.
"""

import re
from pathlib import Path

import fc_common
import fc_script_plan as sp
import fc_snapshot
import fc_stories

FC_DIR = Path(__file__).resolve().parent.parent / "skills" / "frontier-commits"
RENDER_PY = Path(__file__).resolve().parent.parent / "skills" / "daily-podcast" / "render.py"


def _skill_text():
    return (FC_DIR / "SKILL.md").read_text()


def _section(heading: str) -> str:
    """The text of one `## ...` section, up to the next same-level heading.

    Section-scoped rather than whole-file: "this section must not mention Spotify"
    is only meaningful if a mention elsewhere in the document can't satisfy it.
    """
    text = _skill_text()
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


def _first_table_after(marker: str) -> list[list[str]]:
    """Cell-parse the first markdown table after the line containing `marker`.

    Returns data rows only (header and separator dropped), every cell stripped
    of whitespace and backticks.
    """
    lines = _skill_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if marker in ln), None)
    assert start is not None, f"SKILL.md lost the {marker!r} section"
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    assert i < len(lines), f"SKILL.md has no table after {marker!r}"
    rows = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        cells = [c.strip().strip("`").strip() for c in lines[i].strip().strip("|").split("|")]
        rows.append(cells)
        i += 1
    return rows[2:]  # drop the header row and the |---| separator


def test_skill_md_shape_table_matches_the_code():
    names = list(sp.STORY_SHAPES_W)
    lines = _skill_text().splitlines()
    header = next((i for i, ln in enumerate(lines) if "| pos 0 |" in ln), None)
    assert header is not None, "SKILL.md lost the per-position shape table"
    body = lines[header + 2 : header + 2 + len(sp.SHAPE_ORDERS_W)]
    for row, (order, line) in enumerate(zip(sp.SHAPE_ORDERS_W, body, strict=True)):
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        assert cells[0] == str(row)
        assert cells[1:] == [names[i] for i in order]


def test_skill_md_bank_tables_match_the_code_cell_for_cell():
    banks = [
        ("### Cold open", list(sp.INTRO_MODES_W)),
        ("### Sign-off", list(sp.OUTRO_MODES_W)),
        ("What each shape means:", list(sp.STORY_SHAPES_W)),
        ("### Segues", list(sp.MOVES_W)),
    ]
    for marker, expected in banks:
        rows = _first_table_after(marker)
        got = [r[1] for r in rows]
        assert got == expected, f"bank table after {marker!r}: {got} != {expected}"
        indices = [r[0] for r in rows]
        assert indices == [str(i) for i in range(len(expected))], (
            f"bank table after {marker!r} is mis-numbered: {indices}"
        )


def test_skill_md_story_type_table_matches_the_code():
    rows = _first_table_after("## Story types")
    assert [r[0] for r in rows] == list(fc_stories.TYPE_PRIORITY), (
        "story-type table rows do not match fc_stories.TYPE_PRIORITY in order"
    )
    assert [r[1] for r in rows] == [str(v) for v in fc_stories.TYPE_PRIORITY.values()], (
        "story-type table priorities drifted from fc_stories.TYPE_PRIORITY"
    )


def test_skill_md_setup_documents_every_default_config_key():
    m = re.search(r"^## Setup$(.*)\Z", _skill_text(), re.M | re.S)
    assert m, "SKILL.md lost its Setup section"
    setup = m.group(1)
    documented = set(re.findall(r'^\s{1,3}"(\w+)"\s*:', setup, re.M))
    # Every key is a DEFAULT_CONFIG key now. `show_id` used to be the one extra —
    # required, no default — and left with #155: an RSS-first show has no Spotify
    # show to upload to, so documenting one invites an operator to create it.
    expected = set(fc_common.DEFAULT_CONFIG)
    missing = expected - documented
    assert not missing, f"Setup config example is missing config keys: {sorted(missing)}"
    phantom = documented - expected
    assert not phantom, f"Setup config example documents unknown keys: {sorted(phantom)}"


def test_fc_snapshot_cli_exists_and_matches_the_documented_contract():
    assert callable(fc_snapshot.main)
    source = (FC_DIR / "fc_snapshot.py").read_text()
    skill = _skill_text()
    for literal in ("SNAPSHOT ok date=", "SNAPSHOT FAILED"):
        assert literal in source, f"fc_snapshot.py lost its {literal!r} final-line contract"
        assert literal in skill, f"SKILL.md no longer documents the {literal!r} final line"


def test_render_py_honors_the_documented_manifest_keys():
    render_src = RENDER_PY.read_text()
    for key in (
        "r2_manifest_name",
        "r2_key_prefix",
        "ship_mode",
        "show_name",
        "description_footer_text",
        "cover_image",
    ):
        assert key in render_src, (
            f"SKILL.md's manifest promises {key} but render.py never mentions it"
        )


# --- web-only shipping (#155) ----------------------------------------------
#
# Frontier Commits is RSS-first: the R2/RSS publish IS the ship and the show's
# save-to-spotify show is deprecated. SKILL.md is the production path — the
# scheduled weekly run is a Claude invocation following it — so a mode that
# exists only in render.py never reaches a real episode.


def test_skill_md_pins_the_shows_own_cover_name():
    # render.py stamps the cover from ~/.config/daily-podcast/config.json unless the
    # MANIFEST overrides it (#157), so an example without this key produces episodes
    # whose art carries the daily show's branding. Section-scoped on purpose: the
    # Setup section's frontier config.json carries an identical show_name line, and
    # that file is precisely the one render.py does not read.
    assert '"show_name": "Frontier Commits"' in _section("## Manifest"), (
        "SKILL.md's manifest example must set show_name; without it every cover "
        "renders with the daily show's name"
    )


def test_skill_md_pins_the_shows_own_episode_art():
    # #157 swapped the NAME on the daily show's cover template; the ART stayed the
    # daily show's — its purple/orange gradient reached every podcast client that
    # renders per-episode images, under a channel image that is this show's real
    # cover. cover_image points render.py at the bundled art instead (#164).
    # Section-scoped like the other cover pin: the Manifest example is what the
    # weekly run copies.
    assert '"cover_image"' in _section("## Manifest"), (
        "SKILL.md's manifest example must set cover_image; without it every episode "
        "ships the daily show's generated gradient as its artwork"
    )


def test_the_bundled_show_art_is_a_valid_podcast_cover():
    # The asset is a copy of cortech.online's public/frontier-commits-cover.jpg —
    # bundled, not fetched, because a render must not depend on the network for a
    # local artifact (same posture as the house-voice ref clip). Apple Podcasts and
    # Spotify both require square art, 1400-3000px.
    from PIL import Image

    art = FC_DIR / "refs" / "cover.jpg"
    assert art.exists(), "the bundled Frontier Commits show art is missing"
    with Image.open(art) as im:
        assert im.format == "JPEG"
        assert im.width == im.height, f"show art must be square (got {im.size})"
        assert 1400 <= im.width <= 3000, f"show art must be 1400-3000px (got {im.width})"


def test_skill_md_pins_the_shows_own_source_credit_footer():
    # render.py appends the DAILY show's credit line (the OPML feeds, curated in
    # Don't Hype Me) to every description unless the manifest overrides it
    # (#152) — factually wrong attribution in this show's public RSS show notes,
    # whose sources are GitHub orgs linked per chapter. Section-scoped like the
    # cover-name pin: the Manifest example is what the weekly run copies.
    assert '"description_footer_text"' in _section("## Manifest"), (
        "SKILL.md's manifest example must set description_footer_text; without it "
        "every episode's public show notes credit the daily show's sources"
    )


def test_skill_md_pins_the_web_only_ship_mode():
    assert '"ship_mode": "web"' in _skill_text(), (
        "SKILL.md's manifest example must set ship_mode=web; without it render.py "
        "defaults to a Spotify upload against the deprecated show"
    )


def test_render_py_defaults_to_spotify_so_the_daily_show_is_unaffected():
    import render

    assert render.resolve_ship_mode({}) == render.SHIP_MODE_SPOTIFY
    assert render.is_web_only({"ship_mode": "web"}) is True


def test_weekly_run_section_ships_to_the_web_and_never_polls_spotify():
    section = _section("## Unattended weekly run")
    assert "r2=ok" in section, "the report line must pin r2=ok — the publish is the ship"
    assert "<mp3_url>" in section, "the SHIPPED line reports the public mp3 URL"
    for stale in ("READY", "save-to-spotify", "spotify:show:"):
        assert stale not in section, (
            f"the weekly run still references {stale!r}; this show does not ship to Spotify"
        )


def test_setup_no_longer_tells_the_operator_to_create_a_spotify_show():
    setup = _section("## Setup")
    assert "save-to-spotify --json shows" not in setup, (
        "Setup still walks the operator through creating a Spotify show, which is "
        "obsolete for an RSS-first show"
    )


def test_skill_md_relative_paths_resolve():
    referenced = set(re.findall(r"`\./([^`]+)`", _skill_text()))
    required = {
        "fc_common.py",
        "fc_snapshot.py",
        "fc_stories.py",
        "fc_script_plan.py",
        "prompts/weekly.md",
        "prompts/write_story.md",
        "launchd/com.cortech.frontier-commits-snapshot.plist",
    }
    missing_refs = required - referenced
    assert not missing_refs, f"SKILL.md's Layout no longer references: {sorted(missing_refs)}"
    for rel in sorted(referenced):
        assert (FC_DIR / rel).exists(), f"SKILL.md references ./{rel} but it does not exist"


def test_weekly_prompt_stays_a_stub():
    stub = (FC_DIR / "prompts" / "weekly.md").read_text()
    assert "SKILL.md" in stub
    for marker in ("fc_stories.py detect", "Assemble", "trend-watch"):
        assert marker not in stub, f"prompts/weekly.md re-inlined the procedure: {marker!r}"
    assert len(stub.splitlines()) < 40


def test_write_story_prompt_declares_the_placeholders():
    text = (FC_DIR / "prompts" / "write_story.md").read_text()
    for ph in (
        "<<TYPE>>",
        "<<TITLE>>",
        "<<URL>>",
        "<<FACTS>>",
        "<<SHAPE>>",
        "<<MIN_CHARS>>",
        "<<MAX_CHARS>>",
    ):
        assert ph in text


def test_skill_md_pins_the_frontier_manifest_name():
    assert "manifest-frontier-commits.json" in _skill_text()


def test_skill_md_pins_the_frontier_key_prefix():
    # Without the prefix, a frontier publish mints the daily show's same-day
    # <slug>.mp3/<slug>.jpg keys in the shared bucket and overwrites them (#142).
    assert '"r2_key_prefix": "frontier-commits/"' in _skill_text()


def test_skill_md_title_style_is_topic_first():
    # The episode title names the week's lead stories, never the generic
    # "Frontier Commits — Week of ..." form (show name is already on every
    # directory listing; the first ~30 chars are the browsing budget).
    text = _skill_text()
    assert "<topic>, <topic>, <topic> - Week of <Month D, YYYY>" in text
    import re

    example = re.search(r'"title": "([^"]+)"', text)
    assert example, "Manifest example lost its title line"
    assert not example.group(1).startswith("Frontier Commits"), (
        "the Manifest example's title regressed to the generic show-name form"
    )
