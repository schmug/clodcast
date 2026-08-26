"""The discussion desk's content rules (#176, spec section 4.4).

Amended from the spec's original `test_callers.py`: the recon (#173) established
that /feed/comments carries NO comment bodies, so the caller role was dropped
entirely. What is left is a desk that reports the BOARD - how many called and
from which instances - and these tests are what stop it reporting anything else.

Every rule here is enforced in code rather than asked for in prose, because the
failure mode is fabrication on a public feed: a synthesised voice attributing
words to a real, named person we never read. The permalinks used below are the
real ones captured in tests/data/bubbles_feed_comments.xml.
"""

import pathlib

import pytest

import st_script_plan
import st_write

DATA = pathlib.Path(__file__).resolve().parent / "data"


def _comment_entries():
    feedparser = pytest.importorskip("feedparser")
    return feedparser.parse(str(DATA / "bubbles_feed_comments.xml")).entries


def _post(**over) -> dict:
    post = {
        "title": "NO THANK YOU.",
        "url": "https://example.com/no-thank-you",
        "summary": "A short and furious post.",
        "comment_count": 1,
        "domain": "example.com",
    }
    post.update(over)
    return post


def _scene_casting_the_switchboard() -> dict:
    """A plan scene in which the switchboard actually holds a seat."""
    for pos in range(5):
        scene = st_script_plan.build_scene(week=3, pos=pos)
        if scene["roles"].get("switchboard"):
            return scene
    raise AssertionError("no scene in the cycle casts the switchboard")


def _lines(voice: str, text: str) -> list[dict]:
    """One switchboard turn plus enough clean panel text to clear the floor."""
    others = [v for v in st_script_plan.VOICES_ST if v != voice]
    filler = "The post argues its case at length and the panel takes it seriously. " * 4
    return [
        {"speaker": others[0], "text": filler},
        {"speaker": voice, "text": text},
        {"speaker": others[1], "text": filler},
    ]


# --- there is no caller, anywhere -------------------------------------------


def test_the_cast_has_no_caller_seat():
    assert "caller" not in [v.lower() for v in st_script_plan.VOICES_ST]
    assert "caller" not in st_script_plan.ROLES_ST


def test_a_caller_speaker_never_reaches_a_built_manifest():
    scene = _scene_casting_the_switchboard()
    lines = _lines("caller", "A listener called in to say the post is wrong.")
    with pytest.raises(SystemExit):
        st_write.assemble_manifest(
            "2026-08-31",
            "A title",
            "A summary.",
            [{"kind": "scene", "post": _post(), "plan": scene, "lines": lines}],
        )


def test_a_caller_speaker_is_refused_before_it_can_be_assembled():
    got = st_write.classify_scene(
        '{"ok": true, "lines": [{"speaker": "caller", "text": "%s"}]}' % ("x" * 600),
        "",
        0,
    )
    assert got["outcome"] == "REFUSED"
    assert "caller" in got["detail"]


# --- host, never handle ------------------------------------------------------


def test_the_board_reports_instance_hosts_and_never_handles():
    entries = _comment_entries()
    facts = st_write.board_facts(_post(comment_count=3), entries)
    assert facts["hosts"], "the fixture's permalinks carry instance hosts"
    for host in facts["hosts"]:
        assert "@" not in host
    brief = st_write.board_brief(facts)
    for handle in ("@snowgoon", "@clare_hooley", "@jerryorr", "@numericcitizen"):
        assert handle not in brief


@pytest.mark.parametrize(
    "handle",
    [
        "@numericcitizen@techhub.social",  # the full user@host shape
        "@numericcitizen",  # the bare shape a writer is likelier to produce
    ],
)
def test_a_switchboard_line_naming_a_handle_is_refused(handle):
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, f"One call on this one, from {handle}, in overnight.")
    got = st_write.scene_violations(lines, scene, _post(comment_count=1))
    assert got, f"a handle ({handle}) must be a violation"
    assert any("handle" in p for p in got)


def test_a_clean_switchboard_line_is_not_a_violation():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "One call on this one, from a tech server, in overnight.")
    assert st_write.scene_violations(lines, scene, _post(comment_count=1)) == []


# --- the data vetoes the slot ------------------------------------------------


def test_zero_comments_means_no_switchboard_line_survives():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "The board lit up on this one, a couple of calls in overnight.")
    got = st_write.scene_violations(lines, scene, _post(comment_count=0))
    assert any("no comments" in p for p in got)


def test_zero_comments_still_lets_the_rest_of_the_scene_through():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    panel = [ln for ln in _lines(voice, "unused") if ln["speaker"] != voice]
    assert st_write.scene_violations(panel, scene, _post(comment_count=0)) == []


# --- the count claim is checkable, so it is checked --------------------------


def test_a_fabricated_call_count_is_a_violation():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "Four calls on this one, all of them overnight.")
    got = st_write.scene_violations(lines, scene, _post(comment_count=2))
    assert any("count" in p for p in got), "a four-call claim on a two-comment post is fabrication"


def test_the_true_call_count_is_not_a_violation():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "Two calls on this one, both of them overnight.")
    assert st_write.scene_violations(lines, scene, _post(comment_count=2)) == []


def test_a_fabricated_count_never_reaches_a_built_manifest():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "Nine calls came in on this one.")
    with pytest.raises(SystemExit):
        st_write.assemble_manifest(
            "2026-08-31",
            "A title",
            "A summary.",
            [{"kind": "scene", "post": _post(comment_count=1), "plan": scene, "lines": lines}],
        )


def test_an_unsupported_instance_host_is_a_violation():
    # With no comment entries supplied the hosts are simply not available (the
    # count comes off the post's own entry with no extra fetch), so ANY named
    # server is invented.
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "One call on this one, in from techhub.social overnight.")
    got = st_write.scene_violations(lines, scene, _post(comment_count=1))
    assert any("instance" in p for p in got)


def test_a_host_the_comments_feed_confirms_is_allowed():
    scene = _scene_casting_the_switchboard()
    voice = scene["roles"]["switchboard"]
    lines = _lines(voice, "One call on this one, in from techhub.social overnight.")
    got = st_write.scene_violations(
        lines, scene, _post(comment_count=1), comment_entries=_comment_entries()
    )
    assert got == []
