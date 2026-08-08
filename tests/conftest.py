"""
Put `skills/daily-podcast/` on sys.path so `import render` works from the repo root.

The skill ships as a flat directory (no package), so tests use the module directly
rather than restructuring. See CLAUDE.md ("Keep render.py single-file").
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "daily-podcast"
sys.path.insert(0, str(SKILL_DIR))

import render  # noqa: E402  (must follow the sys.path insert above)

# Every user-level state path render.py can WRITE to. Redirected per-test below.
# Read-only paths (the bundled house-voice refs, blocked_sources.json) are left
# alone — tests legitimately assert against the shipped files.
_WRITABLE_STATE_ATTRS = (
    "CONFIG_DIR",
    "CONFIG_PATH",
    "COVERED_PATH",
    "INFLIGHT_PATH",
    "RUN_LOG_PATH",
    "REJECTIONS_PATH",
    "INCIDENT_DIR",
    # TMP_BASE matters as much as the config paths: the auto workdir is now
    # deterministic (daily-podcast-<date>), so an unpatched test would land on the
    # exact directory a real same-day run is using — and the auto-delete-on-success
    # path would then rmtree it.
    "TMP_BASE",
)


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path_factory, monkeypatch):
    """Point every writable user-level path at a throwaway dir, for EVERY test.

    This is a safety net, not a convenience. `main()` writes an incident report on
    any non-clean exit, and a great many tests deliberately drive failure paths —
    so without this, running the suite scribbles into the developer's real
    ~/.config/daily-podcast (it did, once, which is why this exists). Tests that
    need to assert on a specific path still patch it themselves; an explicit patch
    inside a test overrides this fixture.
    """
    sandbox = tmp_path_factory.mktemp("daily-podcast-state")
    monkeypatch.delenv("DAILY_PODCAST_INCIDENT_DIR", raising=False)
    monkeypatch.setattr(render, "CONFIG_DIR", sandbox)
    monkeypatch.setattr(render, "CONFIG_PATH", sandbox / "config.json")
    monkeypatch.setattr(render, "COVERED_PATH", sandbox / "covered.json")
    monkeypatch.setattr(render, "INFLIGHT_PATH", sandbox / "inflight.json")
    monkeypatch.setattr(render, "RUN_LOG_PATH", sandbox / "runs.jsonl")
    monkeypatch.setattr(render, "REJECTIONS_PATH", sandbox / "rejections.jsonl")
    monkeypatch.setattr(render, "INCIDENT_DIR", sandbox / "incidents" / "new")
    workdirs = sandbox / "tmp"
    workdirs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(render, "TMP_BASE", workdirs)
    yield sandbox


def pytest_configure(config):
    """Fail loudly if the guard above is ever removed or bypassed: no test may
    resolve a writable state path inside the real home config directory."""
    real = Path.home() / ".config" / "daily-podcast"
    config._daily_podcast_real_state_dir = real


@pytest.fixture(autouse=True)
def _assert_no_real_state_writes(request, _isolate_user_state):
    yield
    real = request.config._daily_podcast_real_state_dir
    system_tmp = Path(tempfile.gettempdir()).resolve()
    for attr in _WRITABLE_STATE_ATTRS:
        value = Path(getattr(render, attr))
        assert real not in value.parents and value != real, (
            f"test left render.{attr} pointing at the real state dir ({value}); "
            "patch it to a tmp path"
        )
    assert Path(render.TMP_BASE).resolve() != system_tmp, (
        "test left render.TMP_BASE at the system temp dir; a deterministic auto "
        "workdir would then collide with a real same-day run"
    )
