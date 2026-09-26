"""Frame renderer for the episode video (see video.py for the step as a whole).

Takes a JSON-able plan (audio path, chapters already placed, caption phrases, the
show's text) and writes a 1920x1080 30 fps H.264/AAC mp4 plus a thumbnail. The
visual language: a spectrum ring and waveform halo driven by the audio, a particle
field that flows with the voice, a drifting nebula tinted per story, chapter cards
whose headline "decrypts" in, word-by-word captions, and a glitch cut on every
story change.

Every frame is a pure function of (plan, audio analysis, frame index): no state
carries from one frame to the next, which is what lets the render split into
independent frame ranges across processes and still produce one seamless video.
Heavy deps (numpy, OpenCV, Pillow) are imported here and only here — video.py stays
stdlib-only so CI can test the step's contracts without them.
"""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import random
import subprocess
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
SR = 22050
N_BANDS = 48
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONTS = {
    "sg4": "space-grotesk-400.ttf",
    "sg5": "space-grotesk-500.ttf",
    "sg7": "space-grotesk-700.ttf",
    "jb4": "jetbrains-mono-400.ttf",
    "jb5": "jetbrains-mono-500.ttf",
    "jb7": "jetbrains-mono-700.ttf",
}
# (primary, secondary) accents, RGB, cycled per chapter so every story change reads
# as a change even with the sound off.
PALETTES = [
    ((34, 211, 238), (129, 140, 248)),
    ((232, 121, 249), (99, 102, 241)),
    ((251, 191, 36), (244, 63, 94)),
    ((52, 211, 153), (34, 211, 238)),
    ((167, 139, 250), (236, 72, 153)),
    ((248, 113, 113), (251, 146, 60)),
    ((56, 189, 248), (52, 211, 153)),
    ((244, 114, 182), (250, 204, 21)),
]
GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$%&*+=<>/\\|{}[]"
# The bundled fonts are the Latin subset. Anything outside it would draw as a tofu
# box, so text is folded to its closest Latin form first (see clean_text).
_KEEP_RANGES = ((0x20, 0x7E), (0xA0, 0x24F), (0x2000, 0x206F), (0x20AC, 0x20AC))
X264 = ["-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "20"]
X264 += ["-pix_fmt", "yuv420p", "-g", "60"]
# Spotify's video spec asks for AAC-LC >= 192 kbps STEREO; the episode mp3 is mono
# 44.1k by design (render.py), so the mux up-mixes rather than trusting the source.
AAC = ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]


# --- small helpers -----------------------------------------------------------


def clean_text(s: str) -> str:
    out = []
    for ch in s:
        if any(a <= ord(ch) <= b for a, b in _KEEP_RANGES):
            out.append(ch)
            continue
        folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
        out.append(folded)
    return "".join(out)


def bgr(c) -> tuple[int, int, int]:
    return (int(c[2]), int(c[1]), int(c[0]))


def lerp(a, b, k):
    return a + (b - a) * k


def ease(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return 1 - (1 - k) ** 3


def ease_io(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return 4 * k**3 if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2


def fmt_clock(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60:02d}:{t % 60:02d}"


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / FONTS[name]), size)


def decode(text: str, k: float, seed: int) -> str:
    """Scramble-to-reveal for k in [0, 1]: characters resolve left to right."""
    if k >= 1:
        return text
    rnd = random.Random(seed)
    n = len(text)
    out = []
    for i, ch in enumerate(text):
        th = i / max(1, n) * 0.7
        if ch == " ":
            out.append(" ")
        elif k > th + 0.3:
            out.append(ch)
        elif k > th:
            out.append(rnd.choice(GLYPHS))
        else:
            out.append(" " if rnd.random() < 0.5 else "")
    return "".join(out)


def wrap(text: str, fname: str, size: int, maxw: int) -> list[str]:
    f = font(fname, size)
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# --- text sprites ------------------------------------------------------------


@lru_cache(maxsize=4096)
def _sprite(text: str, fname: str, size: int, color: tuple, tracking: int, shadow: bool):
    f = font(fname, size)
    widths = [f.getlength(ch) + tracking for ch in text] if tracking else None
    w = int(sum(widths) if widths else f.getlength(text)) + 8
    asc, desc = f.getmetrics()
    h = asc + desc + 8
    pad = 12 if shadow else 0
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))

    def draw(d, dx, dy, fill):
        if widths:
            x = pad + dx
            for ch, cw in zip(text, widths, strict=True):
                d.text((x, pad + dy), ch, font=f, fill=fill)
                x += cw
        else:
            d.text((pad + dx, pad + dy), text, font=f, fill=fill)

    if shadow:
        draw(ImageDraw.Draw(im), 0, 3, (0, 0, 0, 150))
        arr = np.array(im)
        arr[..., 3] = cv2.GaussianBlur(arr[..., 3], (0, 0), 5)
        im = Image.fromarray(arr)
    draw(ImageDraw.Draw(im), 0, 0, color + (255,))
    return np.array(im)[..., [2, 1, 0, 3]].copy(), pad


def sprite(text, fname, size, color, tracking=0, shadow=True):
    color = tuple(int(c) for c in color)
    return _sprite(clean_text(text), fname, size, color, tracking, shadow)


def blit(frame, spr, x, y, opacity=1.0, anchor="l"):
    """Alpha-blend a BGRA sprite; (x, y) is the text origin, anchor l / m / r."""
    arr, pad = spr
    h, w = arr.shape[:2]
    if anchor == "m":
        x -= (w - 2 * pad) / 2
    elif anchor == "r":
        x -= w - 2 * pad
    x, y = int(round(x - pad)), int(round(y - pad))
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1 or opacity <= 0.003:
        return
    s = arr[y0 - y : y1 - y, x0 - x : x1 - x]
    a = s[..., 3:4].astype(np.float32) * (opacity / 255.0)
    roi = frame[y0:y1, x0:x1]
    roi[:] = (roi * (1 - a) + s[..., :3] * a).astype(np.uint8)


# --- audio analysis ----------------------------------------------------------


def _smooth(x: np.ndarray, attack: float, release: float) -> np.ndarray:
    out = np.empty_like(x)
    cur = x[0].copy() if x.ndim > 1 else x[0]
    for i in range(len(x)):
        v = x[i]
        cur = cur + (v - cur) * np.where(v > cur, attack, release)
        out[i] = cur
    return out


def analyse_audio(path: str) -> dict[str, np.ndarray]:
    """Per-frame spectrum bands, level, onset pulse, flow drive and a waveform
    snapshot. Normalised against the episode's OWN percentiles, so a quiet episode
    moves the visuals as much as a loud one."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    y = np.frombuffer(raw, dtype=np.float32).copy()
    hop = SR / FPS
    n = int(len(y) / hop) + 1
    win = 2048
    pad = np.pad(y, (win, win))
    centers = (np.arange(n) * hop).astype(int) + win
    spec = np.empty((n, win // 2 + 1), np.float32)
    hann = np.hanning(win).astype(np.float32)
    offs = np.arange(-win // 2, win // 2)
    for s in range(0, n, 2048):  # blocked, so a long episode never holds 8 GB of frames
        idx = centers[s : s + 2048, None] + offs[None, :]
        spec[s : s + 2048] = np.abs(np.fft.rfft(pad[idx] * hann, axis=1))
    freqs = np.fft.rfftfreq(win, 1 / SR)
    bi = np.searchsorted(freqs, np.geomspace(70, 8500, N_BANDS + 1))
    for i in range(1, len(bi)):
        bi[i] = max(bi[i], bi[i - 1] + 1)  # low bands narrower than one FFT bin
    bands = np.stack(
        [spec[:, a:b].mean(axis=1) + 1e-9 for a, b in zip(bi[:-1], bi[1:], strict=True)], 1
    )
    db = 20 * np.log10(bands)
    lo, hi = np.percentile(db, 30, axis=0), np.percentile(db, 99.3, axis=0)
    b = _smooth(np.clip((db - lo) / (hi - lo + 1e-6), 0, 1) ** 1.4, 0.65, 0.16)
    rms = np.array(
        [np.sqrt(np.mean(pad[c - 1024 : c + 1024] ** 2)) + 1e-9 for c in centers], np.float32
    )
    rdb = 20 * np.log10(rms)
    rlo, rhi = np.percentile(rdb, 15), np.percentile(rdb, 99.5)
    level = _smooth(np.clip((rdb - rlo) / (rhi - rlo + 1e-6), 0, 1), 0.5, 0.08)
    low = db[:, :10].mean(axis=1)
    flux = np.maximum(0, np.diff(low, prepend=low[0]))
    pulse = _smooth(np.clip(flux / (np.percentile(flux, 99.5) + 1e-9), 0, 1.5), 0.9, 0.07)
    drive = np.cumsum(0.35 + 1.6 * level) / FPS
    wave = pad[centers[:, None] + np.linspace(-600, 600, 256).astype(int)[None, :]]
    wave = np.clip(wave / (np.percentile(np.abs(y), 99.9) + 1e-9), -1.5, 1.5)
    return {
        "bands": b.astype(np.float32),
        "level": level.astype(np.float32),
        "pulse": pulse.astype(np.float32),
        "drive": drive.astype(np.float32),
        "wave": wave.astype(np.float16),
        "duration": np.float32(len(y) / SR),
    }


# --- episode model -----------------------------------------------------------


class Episode:
    def __init__(self, plan: dict[str, Any], analysis: dict[str, np.ndarray]):
        self.plan = plan
        a = analysis
        self.bands, self.level, self.pulse = a["bands"], a["level"], a["pulse"]
        self.drive, self.wave = a["drive"], a["wave"].astype(np.float32)
        self.duration = float(a["duration"])
        self.nframes = int(self.duration * FPS)
        self.chapters = plan["chapters"]
        self.phrases = [(p["s"], p["e"], p["words"]) for p in plan.get("phrases", [])]
        self.disc = self._disc(plan.get("monogram") or "")
        rng = np.random.default_rng(7)
        self.noise = [self._noise(rng, s) for s in (5, 9)]
        self.dots = self._dots()
        self.vignette = self._vignette()
        n = 260
        self.p_ang = rng.uniform(0, 2 * np.pi, n)
        self.p_off = rng.uniform(0, 1, n)
        self.p_spd = rng.uniform(0.05, 0.16, n)
        self.p_sz = rng.uniform(1.0, 3.2, n)
        self.p_swirl = rng.uniform(-0.25, 0.25, n)

    def chapter_at(self, t: float) -> int:
        idx = 0
        for i, c in enumerate(self.chapters):
            if t >= c["t"]:
                idx = i
        return idx

    def _disc(self, monogram: str):
        """The centre disc: the show's monogram over its last word ("CT" / "DAILY")."""
        size = 290
        words = self.plan["show_name"].split()
        im = Image.new("RGB", (size, size), (8, 12, 22))
        d = ImageDraw.Draw(im)
        mono = clean_text(monogram.upper()) or "".join(w[0] for w in words[:2]).upper()
        d.text(
            (size / 2, size / 2 - 8), mono, font=font("sg7", 120), fill=(235, 245, 255), anchor="mm"
        )
        if len(words) > 1:
            sub = clean_text(words[-1].upper())
            d.text(
                (size / 2, size / 2 + 72),
                sub,
                font=font("jb7", 26),
                fill=(34, 211, 238),
                anchor="mm",
            )
        arr = np.array(im)[..., ::-1].copy()
        mask = np.zeros((size, size), np.uint8)
        cv2.circle(mask, (size // 2, size // 2), size // 2 - 1, 255, -1, cv2.LINE_AA)
        return arr, mask.astype(np.float32)[..., None] / 255.0

    @staticmethod
    def _noise(rng, cells):
        # Periodic 1/f noise via the FFT, so the scrolling window never shows a seam.
        h, w = 270, 480
        fy = np.fft.fftfreq(h)[:, None] * h
        fx = np.fft.fftfreq(w)[None, :] * w * 9 / 16
        f = np.sqrt(fx**2 + fy**2)
        amp = 1.0 / np.maximum(f, 1.0) ** (1.6 + cells / 20)
        amp[f > 40] = 0
        acc = np.real(np.fft.ifft2(amp * np.exp(1j * rng.uniform(0, 2 * np.pi, (h, w)))))
        acc = ((acc - acc.min()) / (acc.max() - acc.min())).astype(np.float32)
        return np.tile(acc, (2, 2))

    @staticmethod
    def _dots():
        d = np.zeros((H, W, 3), np.uint8)
        d[18::36, 18::36] = (38, 32, 28)
        d[::3] = np.maximum(d[::3], 4)
        return d

    @staticmethod
    def _vignette():
        yy, xx = np.mgrid[0:135, 0:240].astype(np.float32)
        r = np.sqrt(((xx - 120) / 120) ** 2 + ((yy - 67) / 67) ** 2)
        return np.clip(1.15 - 0.75 * r, 0.12, 1)[..., None]


# --- frame renderer ----------------------------------------------------------


class Renderer:
    def __init__(self, ep: Episode):
        self.ep = ep
        chs = ep.chapters
        # First chapter is the cold open and last the sign-off (render.py's `role`
        # bookends); with fewer than three there is no hero/outro to stage.
        self.bookends = len(chs) >= 3
        self.story_idx: dict[int, int] = {}
        for i in range(len(chs)):
            if not self.bookends or 0 < i < len(chs) - 1:
                self.story_idx[i] = len(self.story_idx) + 1
        self.nstories = len(self.story_idx)
        self._caption_cache: dict = {}

    def palette(self, t):
        ep = self.ep
        ci = ep.chapter_at(t)
        cur = PALETTES[ci % len(PALETTES)]
        prev = PALETTES[(ci - 1) % len(PALETTES)] if ci > 0 else cur
        k = ease_io((t - ep.chapters[ci]["t"]) / 1.2) if ci > 0 else 1
        a = lerp(np.array(prev[0], float), np.array(cur[0], float), k)
        b = lerp(np.array(prev[1], float), np.array(cur[1], float), k)
        return tuple(a), tuple(b)

    def layout(self, t):
        """0 = centred hero (cold open / sign-off), 1 = split (stories)."""
        if not self.bookends:
            return 1.0
        chs = self.ep.chapters
        t1, tout = chs[1]["t"], chs[-1]["t"]
        if t < tout:
            return ease_io((t - (t1 - 0.2)) / 1.3)
        return 1 - ease_io((t - tout) / 1.3)

    def frame(self, fi: int) -> np.ndarray:
        ep = self.ep
        t = fi / FPS
        fc = min(fi, ep.nframes - 1)
        lvl, pulse = float(ep.level[fc]), float(ep.pulse[fc])
        ca, cb = self.palette(t)
        L = self.layout(t)

        # background: two scrolling noise fields tinted by the palette, vignette baked in
        n1, n2 = ep.noise
        ox, oy = int(t * 6) % 480, int(t * 3.5) % 270
        a1 = n1[oy : oy + 270 : 2, ox : ox + 480 : 2]
        ox2, oy2 = int(480 - (t * 4.5) % 480) % 480, int(t * 2.2 + 90) % 270
        a2 = n2[oy2 : oy2 + 270 : 2, ox2 : ox2 + 480 : 2]
        inten = 0.20 + 0.22 * lvl + 0.12 * pulse
        neb = (a1[..., None] ** 2.2) * np.array(bgr(ca), np.float32) * inten + (
            a2[..., None] ** 2.6
        ) * np.array(bgr(cb), np.float32) * (inten * 0.9)
        neb = (neb + np.array((14, 8, 5), np.float32)) * ep.vignette
        frame = cv2.resize(
            np.clip(neb, 0, 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_LINEAR
        )
        frame = cv2.add(frame, ep.dots)

        cx, cy, sc = lerp(960, 520, L), lerp(375, 470, L), lerp(0.80, 1.0, L)
        glow = np.zeros((H // 2, W // 2, 3), np.uint8)
        self.particles(frame, glow, t, fc, cx, cy, ca, cb, sc)
        self.ring(frame, glow, t, fc, ep.bands[fc], lvl, pulse, cx, cy, ca, cb, sc)
        g = cv2.resize(cv2.GaussianBlur(glow, (0, 0), 9), (W, H), interpolation=cv2.INTER_LINEAR)
        frame = cv2.add(cv2.add(frame, g), g)
        self.draw_disc(frame, cx, cy, pulse, sc, ca)

        self.topbar(frame, t, ca)
        self.hero(frame, t, 1 - L, ca)
        self.panel(frame, t, L, ca, cb)
        self.captions(frame, t, ca)
        self.progress(frame, t, ca, cb)
        self.glitch(frame, t, fi)
        return frame

    def particles(self, frame, glow, t, fi, cx, cy, ca, cb, sc):
        ep = self.ep
        prog = (ep.p_off + float(ep.drive[fi]) * ep.p_spd) % 1.0
        r = (175 + prog**1.3 * 900) * sc
        ang = ep.p_ang + ep.p_swirl * prog * 3 + t * 0.03
        xs, ys = cx + np.cos(ang) * r, cy + np.sin(ang) * r * 0.92
        alpha = np.sin(np.pi * prog) ** 1.5
        for i in range(len(xs)):
            if alpha[i] < 0.04:
                continue
            c = tuple(int(v * alpha[i]) for v in bgr(ca if i % 3 else cb))
            s = ep.p_sz[i] * (0.6 + prog[i])
            cv2.circle(glow, (int(xs[i] / 2), int(ys[i] / 2)), max(1, int(s)), c, -1, cv2.LINE_AA)
            bright = tuple(min(255, int(v * 1.2)) for v in c)
            cv2.circle(
                frame, (int(xs[i]), int(ys[i])), max(1, int(s * 0.6)), bright, -1, cv2.LINE_AA
            )

    def ring(self, frame, glow, t, fi, bands, lvl, pulse, cx, cy, ca, cb, sc):
        nb = len(bands)
        vals = np.concatenate([bands, bands[::-1]])  # mirrored: symmetric about the top
        nbar = len(vals)
        r0 = (172 + 10 * pulse) * sc
        rot = t * 0.08
        A, B = np.array(bgr(ca), float), np.array(bgr(cb), float)
        for i in range(nbar):
            v = float(vals[i])
            ang = rot + (i / nbar) * 2 * np.pi - np.pi / 2
            ln = (8 + 150 * v) * sc
            c, s = math.cos(ang), math.sin(ang)
            p0 = (int(cx + c * r0), int(cy + s * r0))
            p1 = (int(cx + c * (r0 + ln)), int(cy + s * (r0 + ln)))
            k = abs(((i if i < nb else nbar - 1 - i) % nb) / nb - 0.5) * 2
            col = A * (1 - k) + B * k
            cv2.line(
                frame,
                p0,
                p1,
                tuple(int(min(255, x * (0.55 + 0.6 * v))) for x in col),
                4,
                cv2.LINE_AA,
            )
            cv2.line(
                glow,
                (p0[0] // 2, p0[1] // 2),
                (p1[0] // 2, p1[1] // 2),
                tuple(int(x * v) for x in col),
                3,
                cv2.LINE_AA,
            )

        # oscilloscope halo: the actual waveform, wrapped around the ring
        w = self.ep.wave[fi]
        w = np.convolve(np.concatenate([w[-4:], w, w[:4]]), np.ones(9) / 9, mode="same")[4:-4]
        rr = (360 + 18 * lvl) * sc
        angs = np.linspace(0, 2 * np.pi, len(w), endpoint=False) - rot * 0.6
        rad = rr + w * 30 * sc * (0.35 + 0.65 * lvl)
        pts = np.round(np.stack([cx + np.cos(angs) * rad, cy + np.sin(angs) * rad], 1)).astype(
            np.int32
        )
        col = tuple(int(x * (0.45 + 0.5 * lvl)) for x in bgr(cb))
        cv2.polylines(frame, [pts], True, col, 2, cv2.LINE_AA)
        cv2.polylines(glow, [pts // 2], True, col, 2, cv2.LINE_AA)

        # HUD arcs, counter-rotating
        c1 = tuple(int(x * 0.7) for x in bgr(ca))
        c2 = tuple(int(x * 0.45) for x in bgr(cb))
        for rad_, th, st, seg, gap, cc in (
            (r0 - 22, 1, t * 25, 12, 18, c1),
            (318 * sc, 1, -t * 14, 40, 30, c2),
            (410 * sc, 2, t * 9, 3, 70, c1),
        ):
            for k in range(int(360 / (seg + gap))):
                a0 = st + k * (seg + gap)
                cv2.ellipse(
                    frame,
                    (int(cx), int(cy)),
                    (int(rad_), int(rad_)),
                    0,
                    a0,
                    a0 + seg,
                    cc,
                    th,
                    cv2.LINE_AA,
                )
        for k in range(4):
            a0 = -t * 20 + k * 90
            cv2.ellipse(
                frame,
                (int(cx), int(cy)),
                (int(440 * sc),) * 2,
                0,
                a0,
                a0 + 14,
                bgr(ca),
                3,
                cv2.LINE_AA,
            )
            cv2.ellipse(
                glow,
                (int(cx / 2), int(cy / 2)),
                (int(220 * sc),) * 2,
                0,
                a0,
                a0 + 14,
                bgr(ca),
                2,
                cv2.LINE_AA,
            )

    def draw_disc(self, frame, cx, cy, pulse, sc, ca):
        arr, m = self.ep.disc
        size = int(arr.shape[0] * sc * (1 + 0.025 * pulse))
        if size != arr.shape[0]:
            arr = cv2.resize(arr, (size, size), interpolation=cv2.INTER_AREA)
            m = cv2.resize(m[..., 0], (size, size), interpolation=cv2.INTER_AREA)[..., None]
        x, y = int(cx - size / 2), int(cy - size / 2)
        roi = frame[y : y + size, x : x + size]
        roi[:] = (roi * (1 - m) + arr * m).astype(np.uint8)
        cv2.circle(frame, (int(cx), int(cy)), size // 2 + 3, bgr(ca), 3, cv2.LINE_AA)

    def _show_words(self):
        words = self.ep.plan["show_name"].upper().split()
        return (words[0], " ".join(words[1:])) if words else ("", "")

    def topbar(self, frame, t, ca):
        first, rest = self._show_words()
        spr = sprite(first, "jb7", 24, (240, 246, 255), tracking=5)
        blit(frame, spr, 64, 44)
        if rest:
            blit(
                frame,
                sprite(rest, "jb4", 24, ca, tracking=5),
                64 + spr[0].shape[1] - 2 * spr[1] + 12,
                44,
            )
        blit(
            frame,
            sprite(self.ep.plan["date_long"].upper(), "jb4", 20, (150, 160, 180), tracking=3),
            64,
            80,
        )
        on = 0.55 + 0.45 * math.sin(t * 4.0)
        cv2.circle(frame, (W - 262, 60), 8, (60, 60, int(150 + 105 * on)), -1, cv2.LINE_AA)
        blit(frame, sprite("ON AIR", "jb7", 22, (240, 246, 255), tracking=4), W - 244, 45)
        tc = f"{fmt_clock(t)} / {fmt_clock(self.ep.duration)}"
        blit(frame, sprite(tc, "jb4", 20, (150, 160, 180)), W - 64, 82, anchor="r")

    def hero(self, frame, t, vis, ca):
        if vis <= 0.01 or not self.bookends:
            return
        plan, chs = self.ep.plan, self.ep.chapters
        outro = t >= chs[-1]["t"]
        base_t = chs[-1]["t"] if outro else 0.4
        k = (t - base_t) / 1.6
        y0 = 700
        seed = int(t * 12)
        if not outro:
            title = decode(clean_text(plan["show_name"].upper()), k, 11 + seed)
            blit(
                frame,
                sprite(title, "sg7", 104, (245, 248, 255), tracking=6),
                960,
                y0 - 30,
                vis,
                "m",
            )
            kk = ease((t - base_t - 1.0) / 0.8)
            blit(
                frame,
                sprite(plan["date_long"].upper(), "jb5", 24, ca, tracking=6),
                960,
                y0 + 100 + 20 * (1 - kk),
                vis * kk,
                "m",
            )
            teaser = plan.get("teaser") or ""
            if teaser:
                kk2 = ease((t - base_t - 1.8) / 0.9)
                line = wrap(clean_text(teaser), "sg4", 32, 1500)[0]
                blit(
                    frame,
                    sprite(line, "sg4", 32, (205, 214, 230)),
                    960,
                    y0 + 150 + 20 * (1 - kk2),
                    vis * kk2,
                    "m",
                )
        else:
            blit(
                frame,
                sprite(
                    decode("THANKS FOR LISTENING", k, 5 + seed),
                    "sg7",
                    80,
                    (245, 248, 255),
                    tracking=4,
                ),
                960,
                y0 - 20,
                vis,
                "m",
            )
            kk = ease((t - base_t - 0.9) / 0.8)
            blit(
                frame,
                sprite(plan.get("outro_url", ""), "jb5", 32, ca, tracking=2),
                960,
                y0 + 78,
                vis * kk,
                "m",
            )
            kk2 = ease((t - base_t - 1.6) / 0.8)
            sub = f"A new episode every morning  ·  Follow {plan['show_name']}"
            blit(frame, sprite(sub, "sg4", 30, (190, 200, 220)), 960, y0 + 138, vis * kk2, "m")

    def panel(self, frame, t, L, ca, cb):
        if L <= 0.01:
            return
        ep = self.ep
        ci = ep.chapter_at(t)
        if ci not in self.story_idx:
            ci = min(self.story_idx) if t < ep.chapters[min(self.story_idx)]["t"] else ci
            if ci not in self.story_idx:
                return
        ch = ep.chapters[ci]
        n = self.story_idx[ci]
        dt = t - ch["t"]
        x0 = 1010 + 60 * (1 - L)
        k_in = ease(dt / 0.7)
        blit(
            frame,
            sprite(f"{n:02d}", "sg7", 150, ca),
            x0 - 8,
            190 + 30 * (1 - k_in),
            L * k_in * 0.95,
        )
        blit(
            frame,
            sprite(f"/ {self.nstories:02d}", "jb5", 30, (130, 140, 165), tracking=2),
            x0 + 190,
            300,
            L * k_in,
        )
        blit(
            frame,
            sprite("NOW COVERING", "jb7", 22, (240, 246, 255), tracking=6),
            x0 + 190,
            262,
            L * k_in,
        )
        cv2.line(
            frame, (int(x0), 392), (int(x0 + 820 * ease(dt / 0.9)), 392), bgr(ca), 2, cv2.LINE_AA
        )
        title = clean_text(ch["title"])
        size = 56
        lines = wrap(title, "sg7", size, 820)
        while len(lines) > 4 and size > 36:
            size -= 4
            lines = wrap(title, "sg7", size, 820)
        lines = lines[:5]
        step = int(size * 1.18)
        kt = (dt - 0.15) / 1.1 * (1 + 0.12 * len(lines))
        for j, line in enumerate(lines):
            txt = decode(line, kt - j * 0.12, ci * 31 + j + int(t * 12))
            blit(frame, sprite(txt, "sg7", size, (246, 248, 255)), x0, 420 + j * step, L)
        y_src = 420 + len(lines) * step + 26
        if ch.get("source"):
            ks = ease((dt - 0.9) / 0.6)
            blit(frame, sprite("SOURCE", "jb7", 18, cb, tracking=4), x0, y_src, L * ks)
            blit(
                frame, sprite(ch["source"], "jb4", 24, (170, 180, 200)), x0 + 110, y_src - 3, L * ks
            )
        nxt = ep.chapters[ci + 1]["t"] if ci + 1 < len(ep.chapters) else ep.duration
        frac = min(1, max(0, dt / max(1, nxt - ch["t"])))
        yb = y_src + 58
        cv2.line(frame, (int(x0), yb), (int(x0 + 820), yb), (60, 55, 50), 2, cv2.LINE_AA)
        cv2.line(frame, (int(x0), yb), (int(x0 + 820 * frac), yb), bgr(cb), 2, cv2.LINE_AA)
        label = f"{fmt_clock(dt)}  /  {fmt_clock(nxt - ch['t'])}"
        blit(frame, sprite(label, "jb4", 18, (120, 130, 150)), x0 + 820, yb + 14, L, "r")

    def captions(self, frame, t, ca):
        ph = None
        for s, e, p in self.ep.phrases:
            if s <= t < e:
                ph = (s, e, p)
                break
            if s > t:
                break
        if not ph:
            return
        s, e, p = ph
        op = min(1, (t - s) / 0.12, (e - t) / 0.15)
        cur = -1
        for i, w in enumerate(p):
            if w["s"] - 0.03 <= t:
                cur = i
        key = (s, cur, tuple(int(x) for x in ca))
        spr = self._caption_cache.get(key)
        if spr is None:
            if len(self._caption_cache) > 256:
                self._caption_cache.clear()
            spr = self._caption_sprite(p, cur, key[2])
            self._caption_cache[key] = spr
        y = 936
        arr, pad = spr
        cw = arr.shape[1] - 2 * pad
        x0, x1 = max(0, int(960 - cw / 2 - 28)), min(W, int(960 + cw / 2 + 28))
        roi = frame[y - 10 : y + 66, x0:x1]
        roi[:] = (roi * (1 - 0.55 * op)).astype(np.uint8)
        blit(frame, spr, 960, y, op, "m")

    @staticmethod
    def _caption_sprite(words, cur, ca):
        f = font("sg5", 46)
        pieces = [
            ((" " if i and w.get("sp", True) else "") + clean_text(w["w"]))
            for i, w in enumerate(words)
        ]
        pad = 12
        im = Image.new(
            "RGBA", (int(f.getlength("".join(pieces))) + 8 + 2 * pad, 76 + 2 * pad), (0, 0, 0, 0)
        )
        d = ImageDraw.Draw(im)
        x = pad
        for i, piece in enumerate(pieces):
            col = ca + (255,) if i == cur else (245, 248, 255, 255 if i < cur else 120)
            d.text((x, pad), piece, font=f, fill=col)
            x += f.getlength(piece)
        return np.array(im)[..., [2, 1, 0, 3]].copy(), pad

    def progress(self, frame, t, ca, cb):
        ep = self.ep
        x0, x1, y = 64, W - 64, 1030
        cv2.line(frame, (x0, y), (x1, y), (70, 62, 56), 3, cv2.LINE_AA)
        xf = int(x0 + (x1 - x0) * min(1.0, t / ep.duration))
        cv2.line(frame, (x0, y), (xf, y), bgr(ca), 3, cv2.LINE_AA)
        for c in ep.chapters[1:]:
            xc = int(x0 + (x1 - x0) * c["t"] / ep.duration)
            cv2.line(
                frame,
                (xc, y - 9),
                (xc, y + 9),
                bgr(cb) if c["t"] <= t else (110, 100, 95),
                2,
                cv2.LINE_AA,
            )
        cv2.circle(frame, (xf, y), 9, bgr(ca), -1, cv2.LINE_AA)
        cv2.circle(frame, (xf, y), 4, (255, 255, 255), -1, cv2.LINE_AA)

    def glitch(self, frame, t, fi):
        ep = self.ep
        ci = ep.chapter_at(t + 0.12)
        if ci == 0:
            return
        dt = t - ep.chapters[ci]["t"]
        if not (-0.12 <= dt < 0.32):
            return
        k = 1 - abs(dt - 0.08) / 0.3
        rnd = random.Random(fi * 7919)  # seeded by frame: identical across re-renders
        for _ in range(int(10 * k) + 2):
            y = rnd.randrange(0, H - 40)
            h = rnd.randrange(6, 60)
            frame[y : y + h] = np.roll(frame[y : y + h], int(rnd.uniform(-70, 70) * k), axis=1)
        off = int(10 * k)
        if off:
            frame[:, :, 2] = np.roll(frame[:, :, 2], off, axis=1)
            frame[:, :, 0] = np.roll(frame[:, :, 0], -off, axis=1)


# --- driver ------------------------------------------------------------------


def _load(workdir: Path) -> Renderer:
    plan = json.loads((workdir / "plan.json").read_text())
    with np.load(workdir / "analysis.npz") as a:
        analysis = {k: a[k] for k in a.files}
    return Renderer(Episode(plan, analysis))


def _render_range(workdir: str, f0: int, f1: int, out: str) -> None:
    r = _load(Path(workdir))
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}"]
        + ["-r", str(FPS), "-i", "-", *X264, "-threads", "2", out],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for fi in range(f0, f1):
            proc.stdin.write(r.frame(fi).tobytes())
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed encoding frames {f0}-{f1}")


def render_video(
    plan: dict[str, Any], out: Path, *, workdir: Path, jobs: int, thumb: Path | None = None
) -> dict[str, Any]:
    """Render `plan` to `out` (mp4) using `jobs` processes; optionally a thumbnail."""
    t0 = time.time()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "plan.json").write_text(json.dumps(plan))
    analysis = analyse_audio(plan["audio"])
    np.savez(workdir / "analysis.npz", **analysis)
    nframes = int(float(analysis["duration"]) * FPS)
    step = math.ceil(nframes / jobs)
    parts = [workdir / f"part{j:02d}.mp4" for j in range(jobs)]
    ctx = mp.get_context("spawn")  # macOS default; explicit so Linux behaves the same
    procs = []
    for j, part in enumerate(parts):
        f0, f1 = j * step, min(nframes, (j + 1) * step)
        if f0 >= f1:
            parts = parts[:j]
            break
        p = ctx.Process(target=_render_range, args=(str(workdir), f0, f1, str(part)))
        p.start()
        procs.append(p)
    for p in procs:
        p.join()
    if any(p.exitcode for p in procs):
        raise RuntimeError("a frame-render worker failed")
    lst = workdir / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
        + ["-i", plan["audio"], "-map", "0:v", "-map", "1:a", "-c:v", "copy", *AAC]
        + ["-movflags", "+faststart", "-shortest", str(out)],
        check=True,
    )
    for p in parts:
        p.unlink(missing_ok=True)
    if thumb is not None:
        r = _load(workdir)
        chs = plan["chapters"]
        t = chs[1]["t"] + 3.0 if len(chs) > 1 else min(10.0, r.ep.duration / 3)
        cv2.imwrite(str(thumb), r.frame(int(t * FPS)), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return {
        "duration_s": round(float(analysis["duration"]), 3),
        "render_s": round(time.time() - t0, 1),
    }
