"""
Put `skills/daily-podcast/` on sys.path so `import render` works from the repo root.

The skill ships as a flat directory (no package), so tests use the module directly
rather than restructuring. See CLAUDE.md ("Keep render.py single-file").
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "daily-podcast"
sys.path.insert(0, str(SKILL_DIR))
