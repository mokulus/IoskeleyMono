"""Score Ioskeley Mono builds against Berkeley Mono reference glyphs.

Layout (override the root with IOSKELEY_ROOT):

    ~/ioskeley/
      TX-02-datasheet.pdf   source of the reference outlines (never commit)
      reference/            extracted reference fonts (never commit)
      Iosevka/              fork of Iosevka, default jj workspace
      IoskeleyMono/         fork of this repo, default jj workspace
      wt/<name>/            per-agent jj workspaces of both repos
      runs/                 score JSON and images
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.freetypePen import FreeTypePen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(os.environ.get("IOSKELEY_ROOT", Path.home() / "ioskeley"))
REF_DIR = ROOT / "reference"
RUNS = ROOT / "runs"
WT = ROOT / "wt"
PLAN = "IoskeleyMono"

# Reference font name in the PDF -> style name used everywhere else.
REF_STYLES = {
    "TX-02-Regular": "Regular",
    "TX-02-Bold": "Bold",
    "TX-02-Oblique": "Italic",
    "TX-02-BoldOblique": "BoldItalic",
}

# Score raster: 4 font units per pixel, covering every Latin glyph box.
SCALE = 0.25
X0, X1 = -100, 700
Y0, Y1 = -300, 1050
BLUR_UNITS = 12.0

ASCII = "".join(chr(c) for c in range(0x21, 0x7F))


# ---------------------------------------------------------------- reference


# Datasheet page 4 samples "Aa" in every cut: 12 weight rows; per width an upright and
# an oblique column. Ioskeley builds only some of them; the rest are skipped.
CUT_PAGE = 3
CUT_WEIGHTS = ["Thin", "ExtraLight", "Light", "SemiLight", "Retina", "Regular", "Book",
               "Medium", "SemiBold", "Bold", "ExtraBold", "Black"]
CUT_COLUMNS = [(w, s) for w in ("Normal", "SemiCondensed", "Condensed", "ExtraCondensed", "UltraCondensed")
               for s in ("Upright", "Italic")]
IOSKELEY_WEIGHTS = {"Thin", "ExtraLight", "Light", "SemiLight", "Regular", "Medium", "SemiBold",
                    "Bold", "ExtraBold", "Black"}
IOSKELEY_WIDTHS = {"Normal", "SemiCondensed"}


def style_name(weight: str, width: str, slope: str) -> str:
    """Iosevka's file suffix: width, weight and slope, each omitted when default."""
    name = ("" if width == "Normal" else width) + ("" if weight == "Regular" else weight) + \
        ("Italic" if slope == "Italic" else "")
    return name or "Regular"


CUT_STYLES = [style_name(wt, wd, sl) for wt in CUT_WEIGHTS if wt in IOSKELEY_WEIGHTS
              for wd, sl in CUT_COLUMNS if wd in IOSKELEY_WIDTHS]


def _write_ref(buf: bytes, style: str, out: Path) -> None:
    from fontTools import agl
    from fontTools.cffLib import CFFFontSet
    from fontTools.fontBuilder import FontBuilder
    from fontTools.misc.psCharStrings import T2WidthExtractor
    from fontTools.ttLib import newTable

    cff = CFFFontSet()
    cff.decompile(io.BytesIO(buf), None)
    top = cff[0]
    order = top.charset
    priv = top.Private
    metrics, cmap = {}, {}
    for gname in order:
        cs = top.CharStrings[gname]
        ex = T2WidthExtractor(getattr(priv, "Subrs", []), cs.globalSubrs, priv.nominalWidthX, priv.defaultWidthX)
        ex.execute(cs)
        bp = BoundsPen(None)
        cs.draw(bp)
        metrics[gname] = (round(ex.width), bp.bounds[0] if bp.bounds else 0)
        uni = agl.toUnicode(gname)
        if len(uni) == 1:
            cmap[ord(uni)] = gname

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=1000, descent=-300)
    fb.setupNameTable({"familyName": "TX02 Reference", "styleName": style})
    fb.setupOS2(sTypoAscender=1000, sTypoDescender=-300, usWinAscent=1000, usWinDescent=300)
    fb.setupPost(isFixedPitch=1)
    fb.setupMaxp()
    table = newTable("CFF ")
    table.cff = cff
    fb.font["CFF "] = table
    fb.font.save(out)
    print(f"{out}  glyphs={len(order)}  mapped={len(cmap)}")


def cmd_ref(args: argparse.Namespace) -> None:
    """Extract the reference fonts from the datasheet into OTF files."""
    import re

    import pymupdf

    doc = pymupdf.open(args.pdf)
    REF_DIR.mkdir(parents=True, exist_ok=True)

    # Full specimens: the largest subset of each named face.
    best: dict[str, bytes] = {}
    for page in range(len(doc)):
        for xref, _ext, _typ, name, *_ in doc.get_page_fonts(page):
            base = name.split("+", 1)[-1]
            if base in REF_STYLES:
                buf = doc.extract_font(xref)[3]
                if len(buf) > len(best.get(base, b"")):
                    best[base] = buf
    for base, buf in best.items():
        _write_ref(buf, REF_STYLES[base], REF_DIR / f"TX02-{REF_STYLES[base]}.otf")

    # Per-cut "Aa" samples. Each text-show operator's font comes from the content stream;
    # its index matches the text trace's seqno.
    page = doc[CUT_PAGE]
    res_to_xref = {f[4]: f[0] for f in page.get_fonts()}
    fonts_in_order, current = [], None
    for m in re.finditer(r"/(\S+)\s+[\d.]+\s+Tf|(Tj|TJ|')(?=\s)", page.read_contents().decode("latin-1")):
        if m.group(1):
            current = m.group(1)
        else:
            fonts_in_order.append(current)
    spans = sorted((s for s in page.get_texttrace() if s["font"] == "TX02-Regular"),
                   key=lambda s: (round(s["bbox"][1]), s["bbox"][0]))
    if len(spans) != len(CUT_WEIGHTS) * len(CUT_COLUMNS):
        sys.exit(f"unexpected page {CUT_PAGE + 1} layout: {len(spans)} samples")
    for i, span in enumerate(spans):
        weight, (width, slope) = CUT_WEIGHTS[i // len(CUT_COLUMNS)], CUT_COLUMNS[i % len(CUT_COLUMNS)]
        style = style_name(weight, width, slope)
        if style not in CUT_STYLES or style in REF_STYLES.values():
            continue  # not built by Ioskeley, or covered by a full specimen
        buf = doc.extract_font(res_to_xref[fonts_in_order[span["seqno"]]])[3]
        _write_ref(buf, style, REF_DIR / f"TX02-{style}.otf")


# ---------------------------------------------------------------- rendering


class Font:
    def __init__(self, path: Path):
        self.path = path
        self.tt = TTFont(path, lazy=True)
        self.cmap = self.tt.getBestCmap()
        self.gs = self.tt.getGlyphSet()

    def has(self, ch: str) -> bool:
        return ord(ch) in self.cmap

    def bounds(self, ch: str):
        bp = BoundsPen(self.gs)
        self.gs[self.cmap[ord(ch)]].draw(bp)
        return bp.bounds

    def render(self, ch: str, scale: float = SCALE) -> np.ndarray:
        """Coverage 0..1, row 0 = top (Y1), column 0 = X0."""
        w = round((X1 - X0) * scale)
        h = round((Y1 - Y0) * scale)
        pen = FreeTypePen(self.gs)
        self.gs[self.cmap[ord(ch)]].draw(pen)
        return pen.array(
            width=w, height=h, transform=(scale, 0, 0, scale, -X0 * scale, -Y0 * scale), contain=False
        ).astype(np.float32)


def _blur(a: np.ndarray, sigma_px: float) -> np.ndarray:
    r = int(3 * sigma_px + 0.5)
    x = np.arange(-r, r + 1)
    k = np.exp(-(x**2) / (2 * sigma_px**2))
    k /= k.sum()
    a = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 0, a)
    return np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, a)


def _soft_iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.maximum(a, b).sum()
    return float(np.minimum(a, b).sum() / union) if union else 1.0


def compare_glyph(ref: Font, cand: Font, ch: str) -> dict:
    r, c = ref.render(ch), cand.render(ch)
    sigma = BLUR_UNITS * SCALE
    iou = _soft_iou(r, c)
    blur_iou = _soft_iou(_blur(r, sigma), _blur(c, sigma))
    rb, cb = ref.bounds(ch), cand.bounds(ch)
    delta = [round(cv - rv) for rv, cv in zip(rb, cb)] if rb and cb else None
    ink_r, ink_c = float(r.sum()), float(c.sum())
    return {
        "score": round((iou + blur_iou) / 2, 4),
        "iou": round(iou, 4),
        "blur_iou": round(blur_iou, 4),
        "ink_ratio": round(ink_c / ink_r, 3) if ink_r else None,
        "bbox_ref": [round(v) for v in rb] if rb else None,
        "bbox_delta": delta,
    }


def overlay(ref: Font, cand: Font, ch: str, scale: float) -> Image.Image:
    """Black = both, red = reference only, blue = candidate only."""
    r, c = ref.render(ch, scale), cand.render(ch, scale)
    rgb = np.ones(r.shape + (3,), np.float32)
    rgb[..., 0] -= c  # candidate removes red
    rgb[..., 1] -= np.maximum(r, c)
    rgb[..., 2] -= r  # reference removes blue
    img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    w = img.width
    for y_units, col in ((0, (0, 160, 0)),):
        y = round((Y1 - y_units) * scale)
        d.line([(0, y), (w, y)], fill=col)
    for x_units in (0, 600):
        x = round((x_units - X0) * scale)
        d.line([(x, 0), (x, img.height)], fill=(200, 200, 200))
    return img


def glyph_set(spec: str, ref: Font) -> str:
    if spec == "ascii":
        chars = ASCII
    elif spec == "all":
        chars = "".join(sorted({chr(u) for u in ref.cmap if u > 0x20}))
    else:
        chars = spec
    return "".join(ch for ch in dict.fromkeys(chars) if ref.has(ch))


def _label(ch: str) -> str:
    return f"U+{ord(ch):04X}"


# ---------------------------------------------------------------- commands


def candidate_path(args) -> Path:
    return Path(args.font) if args.font else _font_file(args.ws, args.style)


def score_font(style: str, font_path: Path, spec: str = "all") -> dict:
    ref = Font(REF_DIR / f"TX02-{style}.otf")
    cand = Font(font_path)
    chars = glyph_set(spec, ref)
    missing = [ch for ch in chars if not cand.has(ch)]
    glyphs = {ch: compare_glyph(ref, cand, ch) for ch in chars if cand.has(ch)}
    summary = {
        "style": style,
        "font": str(font_path),
        "glyphs": len(glyphs),
        "missing": missing,
        "mean_score": round(float(np.mean([g["score"] for g in glyphs.values()])), 4),
        "mean_iou": round(float(np.mean([g["iou"] for g in glyphs.values()])), 4),
    }
    return {"summary": summary, "glyphs": glyphs}


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def cmd_score(args) -> None:
    result = score_font(args.style, candidate_path(args), args.glyphs)
    if args.out:
        _write_json(Path(args.out), result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    glyphs = result["glyphs"]
    rows = sorted(glyphs.items(), key=lambda kv: kv[1]["score"])[: args.top or None]
    print(f"{'ch':>3} {'score':>6} {'iou':>6} {'blur':>6} {'ink':>5}  bbox_delta(xmin,ymin,xmax,ymax)")
    for ch, g in rows:
        print(f"{ch:>3} {g['score']:6.3f} {g['iou']:6.3f} {g['blur_iou']:6.3f} {g['ink_ratio']:5.2f}  {g['bbox_delta']}")
    print(json.dumps(result["summary"], ensure_ascii=False))


def gate(base: dict, new: dict, targets: str, tol: float, show: float = 0.002) -> tuple[bool, list[float], float]:
    """Print score movement for one style. Returns (no regressions, target deltas, total delta)."""
    a, b = base["glyphs"], new["glyphs"]
    deltas = {ch: b[ch]["score"] - a[ch]["score"] for ch in a if ch in b}
    tset = [ch for ch in dict.fromkeys(targets or "") if ch in deltas]
    regressions = {ch: d for ch, d in deltas.items() if d < -tol and ch not in tset}
    print(f"[{new['summary']['style']}] mean {base['summary']['mean_score']:.4f} -> {new['summary']['mean_score']:.4f}"
          f" ({float(np.mean(list(deltas.values()))):+.4f} over {len(deltas)} glyphs)")
    for ch in tset:
        print(f"  target {ch!r}: {a[ch]['score']:.4f} -> {b[ch]['score']:.4f} ({deltas[ch]:+.4f})")
    for d, ch in sorted(((d, ch) for ch, d in deltas.items() if abs(d) >= show and ch not in tset), reverse=True):
        print(f"  {ch!r}: {d:+.4f}")
    if regressions:
        print("  REGRESSIONS: " + " ".join(f"{ch!r}{d:+.4f}" for ch, d in sorted(regressions.items(), key=lambda kv: kv[1])))
    return not regressions, [deltas[ch] for ch in tset], sum(deltas.values())


def verdict(results: list[tuple[bool, list[float], float]], targets: str | None) -> bool:
    """Pass when no style regresses and the work improved: every target present
    in any style went up, or, without targets, the total score went up."""
    clean = all(r[0] for r in results)
    target_deltas = [d for r in results for d in r[1]]
    if targets:
        improved = bool(target_deltas) and all(d > 0 for d in target_deltas)
    else:
        improved = sum(r[2] for r in results) > 0
    ok = clean and improved
    print("GATE PASS" if ok else "GATE FAIL")
    return ok


def cmd_diff(args) -> None:
    r = gate(json.loads(Path(args.base).read_text()), json.loads(Path(args.new).read_text()), args.target, args.tol, args.show)
    sys.exit(0 if verdict([r], args.target) else 1)


def cmd_show(args) -> None:
    ref = Font(REF_DIR / f"TX02-{args.style}.otf")
    cand = Font(candidate_path(args))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for ch in glyph_set(args.glyphs, ref):
        if cand.has(ch):
            path = out / f"{_label(ch)}.png"
            overlay(ref, cand, ch, args.scale).save(path)
            print(path)


def cmd_sheet(args) -> None:
    ref = Font(REF_DIR / f"TX02-{args.style}.otf")
    cand = Font(candidate_path(args))
    chars = [ch for ch in glyph_set(args.glyphs, ref) if cand.has(ch)]
    if args.scores:
        scores = json.loads(Path(args.scores).read_text())["glyphs"]
    else:
        scores = {ch: compare_glyph(ref, cand, ch) for ch in chars}
    tiles = [overlay(ref, cand, ch, args.scale) for ch in chars]
    tw, th = tiles[0].size
    label_h = 18
    cols = args.cols
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + label_h)), "white")
    d = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=14)
    for i, (ch, tile) in enumerate(zip(chars, tiles)):
        x, y = (i % cols) * tw, (i // cols) * (th + label_h)
        sheet.paste(tile, (x, y + label_h))
        s = scores.get(ch, {}).get("score")
        d.text((x + 4, y + 2), f"{ch} {s:.3f}" if s is not None else ch, fill="black", font=font)
    sheet.save(args.out)
    print(args.out)


def _measure(font: Font) -> dict:
    def runs(ch: str, axis: str, at: float) -> list[tuple[int, int]]:
        a = font.render(ch, 1.0) > 0.5
        if axis == "row":  # horizontal runs at y = at
            line = a[round(Y1 - at)]
            off = X0
        else:  # vertical runs at x = at
            line = a[:, round(at - X0)][::-1]
            off = Y0
        out, start = [], None
        for i, v in enumerate(np.append(line, False)):
            if v and start is None:
                start = i
            elif not v and start is not None:
                out.append((start + off, i - start))
                start = None
        return out

    def b(ch, i):
        return round(font.bounds(ch)[i])

    def stem_center(ch, y):
        x, w = runs(ch, "row", y)[0]
        return x + w / 2

    import math

    xh, cap = b("x", 3), b("I", 3)
    probes = {
        "cap_I": lambda: cap,
        "xheight_x": lambda: xh,
        "ascender_d": lambda: b("d", 3),
        "descender_p": lambda: b("p", 1),
        "overshoot_O": lambda: b("O", 3) - cap,
        "overshoot_o": lambda: b("o", 3) - xh,
        "slant_deg_I": lambda: round(math.degrees(math.atan2(
            stem_center("I", 0.7 * cap) - stem_center("I", 0.3 * cap), 0.4 * cap)), 1),
        "H_left_stem": lambda: runs("H", "row", cap / 4)[0][1],
        "H_crossbar": lambda: next(w for y, w in runs("H", "col", 300) if 0.2 * cap < y < 0.8 * cap),
        "n_left_stem": lambda: runs("n", "row", xh / 2)[0][1],
        "o_top_stroke": lambda: runs("o", "col", 300)[-1][1],
        "o_side_stroke": lambda: runs("o", "row", xh / 2)[0][1],
        "H_sidebearings": lambda: [b("H", 0), 600 - b("H", 2)],
        "n_sidebearings": lambda: [b("n", 0), 600 - b("n", 2)],
        "o_sidebearings": lambda: [b("o", 0), 600 - b("o", 2)],
        "period_bbox": lambda: [round(v) for v in font.bounds(".")],
        "paren_bbox": lambda: [round(v) for v in font.bounds("(")],
    }
    m = {}
    for k, f in probes.items():
        try:
            m[k] = f()
        except (KeyError, IndexError, StopIteration):
            m[k] = None
    return m


def cmd_measure(args) -> None:
    ref = _measure(Font(REF_DIR / f"TX02-{args.style}.otf"))
    cand = _measure(Font(candidate_path(args)))
    print(f"{'metric':<16} {'reference':>14} {'candidate':>14}")
    for k in ref:
        print(f"{k:<16} {str(ref[k]):>14} {str(cand[k]):>14}")


# ---------------------------------------------------------------- workspaces


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


STYLES = ["Regular", "Bold", "Italic", "BoldItalic"]
# Current focus: Normal width, Regular weight. Pass --styles to include others.
DEFAULT_STYLES = ["Regular"]

# Scoped builds contain the code-point ranges listed in Iosevka's verdafile (Latin,
# punctuation, arrows, math, box drawing) and the glyphs they depend on, with no OpenType
# features (about 1 s per style). `match --full` builds what a release would contain.
FULL = False


def _ws_root(ws: str | None) -> Path:
    return WT / ws if ws else ROOT


def _plan_file(ws: str | None) -> Path:
    return _ws_root(ws) / "IoskeleyMono" / "private-build-plans.toml"


def _font_file(ws: str | None, style: str) -> Path:
    kind = "TTF-Unhinted" if FULL else "TTF-Scoped"
    return _ws_root(ws) / "Iosevka" / "dist" / PLAN / kind / f"{PLAN}-{style}.ttf"


def _runs(ws: str | None) -> Path:
    return RUNS / (ws or "main")


def _check_plan(path: Path) -> None:
    """Every plan repeats metricOverride (Iosevka cannot inherit it); they must stay identical."""
    import tomllib

    plans = tomllib.loads(path.read_text())["buildPlans"]
    mos = {name: p.get("metricOverride") for name, p in plans.items()}
    first = mos[PLAN]
    bad = [name for name, mo in mos.items() if mo != first]
    if bad:
        sys.exit(f"metricOverride differs from {PLAN} in: {', '.join(bad)} ({path})")


def build(ws: str | None, styles: list[str], quiet: bool = False) -> None:
    """One verda session for all styles: each font compiles in its own process, in parallel."""
    _check_plan(_plan_file(ws))
    ios = _ws_root(ws) / "Iosevka"
    entry = "single" if FULL else "scoped"
    cmd = ["node", "node_modules/verda/bin/verda", "-f", "verdafile.mjs", *(f"{entry}::{PLAN}-{s}" for s in styles)]
    if quiet:
        r = subprocess.run(cmd, cwd=ios, capture_output=True, text=True)
        if r.returncode:
            sys.exit(r.stdout[-4000:] + r.stderr[-4000:])
    else:
        _run(cmd, cwd=ios)


def cmd_build(args) -> None:
    build(args.ws, args.styles)
    for style in args.styles:
        print(_font_file(args.ws, style))


def cmd_check(args) -> None:
    """Build, score every style, and gate against the workspace base scores."""
    build(args.ws, args.styles, quiet=True)
    results = []
    for style in args.styles:
        new = score_font(style, _font_file(args.ws, style))
        _write_json(_runs(args.ws) / "last" / f"{style}.json", new)
        base_path = _runs(args.ws) / "base" / f"{style}.json"
        if not base_path.exists() or args.rebase:
            _write_json(base_path, new)
            print(f"[{style}] base recorded: mean {new['summary']['mean_score']:.4f}")
            continue
        results.append(gate(json.loads(base_path.read_text()), new, args.target, args.tol))
    if not results:
        return
    sys.exit(0 if verdict(results, args.target) else 1)


def cmd_accept(args) -> None:
    """Promote the last check to the new base (one hill-climb step)."""
    last = _runs(args.ws) / "last"
    for f in sorted(last.glob("*.json")):
        shutil.copy(f, _runs(args.ws) / "base" / f.name)
        print(f"base <- {f}")


def cmd_verify(args) -> None:
    """Build scoped and full fonts from the same sources; every scored glyph must match."""
    global FULL
    bad = []
    for full in (False, True):
        FULL = full
        build(args.ws, args.styles, quiet=True)
    for style in args.styles:
        FULL = False
        scoped = score_font(style, _font_file(args.ws, style))["glyphs"]
        FULL = True
        complete = score_font(style, _font_file(args.ws, style))["glyphs"]
        common = [ch for ch in complete if ch in scoped]
        diff = {ch: (scoped[ch]["score"], complete[ch]["score"])
                for ch in common if scoped[ch]["score"] != complete[ch]["score"]}
        absent = "".join(ch for ch in complete if ch not in scoped)
        print(f"[{style}] {len(common)} glyphs compared, {len(diff)} differ"
              + (f"; not in scoped builds: {absent}" if absent else "")
              + "".join(f"\n  {ch!r}: scoped {a} full {b}" for ch, (a, b) in diff.items()))
        bad += diff
    print("VERIFY PASS" if not bad else "VERIFY FAIL")
    sys.exit(1 if bad else 0)


def cmd_ws_new(args) -> None:
    """Create jj workspaces of both repos under wt/<name> with a warm build cache.

    Workspaces start from a committed revision (default @-), never from the main
    checkout's working-copy commit: every edit there rewrites that commit, which makes
    child workspaces stale, and `jj workspace update-stale` overwrites pending edits."""
    dest = WT / args.name
    if dest.exists():
        sys.exit(f"{dest} already exists")
    for repo in ("Iosevka", "IoskeleyMono"):
        dirty = subprocess.run(["jj", "diff", "--summary", "-r", "@"], cwd=ROOT / repo,
                               capture_output=True, text=True, check=True).stdout.strip()
        if dirty and args.rev == "@-":
            sys.exit(f"{ROOT / repo} has uncommitted changes; commit them (jj commit) first:\n{dirty}")
    dest.mkdir(parents=True)
    for repo in ("Iosevka", "IoskeleyMono"):
        _run(["jj", "workspace", "add", "--name", args.name, "-r", args.rev, str(dest / repo)], cwd=ROOT / repo)
    ios = dest / "Iosevka"
    (ios / "private-build-plans.toml").symlink_to("../IoskeleyMono/private-build-plans.toml")
    # Copy-on-write clones on APFS: cheap, and keep the build journal, glyph cache and
    # compiled .ptl output (packages/*/lib, gitignored) warm so nothing recompiles.
    # node_modules links to packages/* are relative, so they resolve inside the copy.
    main = ROOT / "Iosevka"
    for src in [main / ".build", main / "node_modules", *main.glob("packages/*/lib")]:
        _run(["cp", "-Rcp", str(src), str(ios / src.relative_to(main))])
    shutil.rmtree(_runs(args.name), ignore_errors=True)
    if not args.no_base:
        build(args.name, DEFAULT_STYLES, quiet=True)
        for style in DEFAULT_STYLES:
            _write_json(_runs(args.name) / "base" / f"{style}.json", score_font(style, _font_file(args.name, style)))
        print(f"base scores: {_runs(args.name) / 'base'}")
    print(f"workspace ready: {dest}")


def cmd_ws_rm(args) -> None:
    dest = WT / args.name
    # Snapshot both repos first so uncommitted edits become part of <name>@ and survive.
    for repo in ("Iosevka", "IoskeleyMono"):
        if (dest / repo).exists():
            r = subprocess.run(["jj", "status"], cwd=dest / repo, capture_output=True, text=True)
            if r.returncode:
                sys.exit(f"not removing {dest}: `jj status` failed in {repo} ({r.stderr.strip()}).\n"
                         "Do not run `jj workspace update-stale` there: it overwrites pending edits. "
                         "Copy the changed files out first.")
    for repo in ("Iosevka", "IoskeleyMono"):
        # Drop the working-copy commit only if it holds nothing; other work stays in the repo.
        subprocess.run(["jj", "abandon", f"{args.name}@ & empty() & description(exact:'')"], cwd=ROOT / repo, check=False)
        subprocess.run(["jj", "workspace", "forget", args.name], cwd=ROOT / repo, check=False)
    shutil.rmtree(dest, ignore_errors=True)
    for worker in (WT / ".workers").glob(f"{args.name}-*"):
        shutil.rmtree(worker, ignore_errors=True)
    print(f"removed {dest}")


# ---------------------------------------------------------------- variant sweep


def _primes(ws: str | None) -> list[dict]:
    ios = _ws_root(ws) / "Iosevka"
    out = subprocess.run(
        ["node", str(Path(__file__).with_name("variants.mjs")), str(ios)], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def _section_bounds(lines: list[str], section: str) -> tuple[int, int]:
    header = f"[buildPlans.{PLAN}.variants.{section}]"
    start = lines.index(header)
    end = start + 1
    while end < len(lines) and lines[end].strip() and not lines[end].startswith(("[", "#")):
        end += 1
    return start, end


def get_variant(path: Path, key: str, section: str = "design") -> str | None:
    import tomllib

    return tomllib.loads(path.read_text())["buildPlans"][PLAN]["variants"].get(section, {}).get(key)


def set_variant(path: Path, key: str, value: str | None, section: str = "design") -> None:
    """Set (or with None, remove) one variant line, keeping the rest of the file untouched."""
    lines = path.read_text().split("\n")
    start, end = _section_bounds(lines, section)
    idx = next((i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == key), None)
    if value is None:
        if idx is not None:
            del lines[idx]
    elif idx is None:
        lines.insert(end, f'{key} = "{value}"')
    else:
        lines[idx] = f'{key} = "{value}"'
    path.write_text("\n".join(lines))


def cmd_sweep(args) -> None:
    """Try every option of each variant prime; rank by the mean score of the characters it affects."""
    plan = _plan_file(args.ws)
    style = args.style
    ref = Font(REF_DIR / f"TX02-{style}.otf")
    primes = {p["key"]: p for p in _primes(args.ws)}
    keys = args.keys.split(",") if args.keys else [k for k, p in primes.items() if any(ref.has(c) for c in p["hotChars"])]
    if args.shard:
        # Balance shards by option count (largest first into the lightest shard); deterministic.
        i, n = map(int, args.shard.split("/"))
        loads, owner = [0] * n, {}
        for k in sorted(keys, key=lambda k: (-len(primes[k]["variants"]), k)):
            s = loads.index(min(loads))
            owner[k] = s
            loads[s] += len(primes[k]["variants"])
        keys = [k for k in keys if owner[k] == i]
    for key in keys:
        prime = primes[key]
        chars = "".join(c for c in prime["hotChars"] if ref.has(c))
        current = get_variant(plan, key, args.section)
        rows = []
        for option in prime["variants"]:
            set_variant(plan, key, option, args.section)
            try:
                build(args.ws, [style], quiet=True)
            except SystemExit as e:
                print(f"{key}={option}: build failed: {str(e)[-300:]}")
                continue
            res = score_font(style, _font_file(args.ws, style), chars)
            rows.append({"option": option, "mean": res["summary"]["mean_score"],
                         "glyphs": {c: g["score"] for c, g in res["glyphs"].items()}})
            print(f"{key}={option}: {res['summary']['mean_score']:.4f}", flush=True)
        rows.sort(key=lambda r: -r["mean"])
        cur = next((r for r in rows if r["option"] == current), None)
        best = rows[0]
        chosen = current
        if args.apply and best["option"] != current and (cur is None or best["mean"] > cur["mean"] + args.min_gain):
            chosen = best["option"]
        set_variant(plan, key, chosen, args.section)
        _write_json(_runs(args.ws) / "sweep" / f"{key}.json",
                    {"key": key, "chars": chars, "current": current, "chosen": chosen, "ranking": rows})
        print(f"== {key} [{chars}] current={current} ({cur['mean'] if cur else 'n/a'}) "
              f"best={best['option']} ({best['mean']:.4f}) chosen={chosen}", flush=True)
    build(args.ws, [style], quiet=True)


# ---------------------------------------------------------------- global parameter tuning


def _toml_literal(value: str) -> str:
    try:
        float(value)
        return value
    except ValueError:
        return value if value[:1] in "'\"" else json.dumps(value)


def set_plan_value(path: Path, field: str, value: str | None) -> None:
    """Set `field` in the plan text. A bare name is a metricOverride field, set in every plan;
    a dotted path such as buildPlans.IoskeleyMono.slopes.Italic.angle names one table key.
    None removes the key."""
    lines = path.read_text().split("\n")
    if "." in field:
        table, key = field.rsplit(".", 1)
        headers = [f"[{table}]"]
    else:
        key = field
        headers = [ln for ln in lines if ln.startswith("[buildPlans.") and ln.endswith(".metricOverride]")]
    for header in headers:
        start = lines.index(header)
        end = start + 1
        while end < len(lines) and not lines[end].startswith("["):
            end += 1
        idx = next((i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == key), None)
        if value is None:
            if idx is not None:
                del lines[idx]
            continue
        line = f"{key} = {_toml_literal(value)}"
        if idx is not None:
            lines[idx] = line
        else:
            last = max(i for i in range(start, end) if lines[i].strip() and not lines[i].startswith("#"))
            lines.insert(last + 1, line)
    path.write_text("\n".join(lines))


def objective(ws: str | None, styles: list[str], spec: str) -> tuple[float, dict[str, float]]:
    """Mean of per-style mean scores, so each style weighs the same."""
    per = {s: score_font(s, _font_file(ws, s), spec)["summary"]["mean_score"] for s in styles}
    return float(np.mean(list(per.values()))), per


def _sync_worker(ws: str | None, i: int) -> str:
    """Plain-file copy of a workspace used only for building candidates; never a jj checkout.
    Created as an APFS clone (cheap) without jj/git metadata; later syncs copy only changed
    sources and leave the worker's own build caches and outputs alone."""
    wid = f".workers/{ws or 'main'}-{i}"
    src, dst = _ws_root(ws), WT / wid
    if not dst.exists():
        dst.mkdir(parents=True)
        for repo in ("Iosevka", "IoskeleyMono"):
            subprocess.run(["cp", "-Rcp", str(src / repo), str(dst / repo)], check=True)
            for meta in (dst / repo / ".jj", dst / repo / ".git"):
                if meta.is_dir() and not meta.is_symlink():
                    shutil.rmtree(meta)
                elif meta.exists() or meta.is_symlink():
                    meta.unlink()
        return wid
    for repo in ("Iosevka", "IoskeleyMono"):
        excludes = [f"--exclude=/{d}" for d in (".jj", ".git", "dist", ".build", "node_modules")]
        subprocess.run(["rsync", "-a", "--delete", *excludes, f"{src / repo}/", f"{dst / repo}/"], check=True)
    return wid


def _evaluate(wid: str, plan_text: str, field: str, value: str, styles: list[str], spec: str, full: bool):
    """Build and score one candidate in a worker. Runs in its own process (FreeType is not thread-safe)."""
    global FULL
    FULL = full
    plan = _plan_file(wid)
    plan.write_text(plan_text)
    if value != "<current>":
        set_plan_value(plan, field, value)
    build(wid, styles, quiet=True)
    return objective(wid, styles, spec)


def cmd_tune(args) -> None:
    """Try values for one global field in parallel worker copies; keep the best with --apply."""
    from concurrent.futures import ProcessPoolExecutor

    plan = _plan_file(args.ws)
    original = plan.read_text()
    values = ["<current>", *args.values.split(";")]
    jobs = max(1, min(args.jobs, len(values)))
    workers = [_sync_worker(args.ws, i) for i in range(jobs)]
    results = []
    with ProcessPoolExecutor(jobs) as pool:
        for start in range(0, len(values), jobs):  # one value per worker at a time
            wave = values[start:start + jobs]
            futures = [pool.submit(_evaluate, wid, original, args.field, v, args.styles, args.glyphs, FULL)
                       for wid, v in zip(workers, wave)]
            results += [f.result() for f in futures]
    rows = []
    for value, (total, per) in zip(values, results):
        rows.append((total, value, per))
        print(f"{args.field} = {value:<40} {total:.4f}  " + " ".join(f"{s}={v:.4f}" for s, v in per.items()), flush=True)
    current = rows[0]
    best = max(rows, key=lambda r: r[0])
    if args.apply and best is not current and best[0] > current[0] + args.min_gain:
        set_plan_value(plan, args.field, best[1])
        print(f"applied {args.field} = {best[1]} ({current[0]:.4f} -> {best[0]:.4f})")
    else:
        print(f"kept current ({current[0]:.4f}); best {best[1]} ({best[0]:.4f})")
    build(args.ws, args.styles, quiet=True)


# ---------------------------------------------------------------- CLI


def main() -> None:
    p = argparse.ArgumentParser(prog="match", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full", action="store_true", help="build/score complete fonts instead of scoped ASCII builds")
    sub = p.add_subparsers(required=True)

    def cand_args(sp):
        sp.add_argument("--style", default="Regular", choices=sorted(set(STYLES + CUT_STYLES)))
        sp.add_argument("--ws", help="workspace name under wt/ (default: main checkout)")
        sp.add_argument("--font", help="explicit candidate font path")
        sp.add_argument("--glyphs", default="all", help="'all' reference glyphs, 'ascii', or literal characters")

    sp = sub.add_parser("tune", help="try values for one global plan field across styles")
    sp.add_argument("--ws", required=True, help="never tune in the main checkout")
    sp.add_argument("field", help="metricOverride field (all plans) or dotted table path ending in a key")
    sp.add_argument("values", help="';'-separated candidate values (TOML literals or bare strings)")
    sp.add_argument("--styles", nargs="+", default=DEFAULT_STYLES)
    sp.add_argument("--glyphs", default="all")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--min-gain", type=float, default=0.0005)
    sp.add_argument("--jobs", type=int, default=6, help="candidate values evaluated in parallel")
    sp.set_defaults(func=cmd_tune)

    sp = sub.add_parser("ref", help="extract reference fonts from the datasheet")
    sp.add_argument("--pdf", default=str(ROOT / "TX-02-datasheet.pdf"))
    sp.set_defaults(func=cmd_ref)

    sp = sub.add_parser("build", help="build single styles in a workspace")
    sp.add_argument("--ws")
    sp.add_argument("styles", nargs="*", default=["Regular"])
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("check", help="build + score + gate against the workspace base (one hill-climb step)")
    sp.add_argument("--ws")
    sp.add_argument("--target", help="characters being worked on; each must improve")
    sp.add_argument("--tol", type=float, default=0.003, help="allowed drop for non-target glyphs")
    sp.add_argument("--styles", nargs="+", default=DEFAULT_STYLES)
    sp.add_argument("--rebase", action="store_true", help="record this build as the base instead of gating")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("accept", help="promote the last check to the workspace base")
    sp.add_argument("--ws")
    sp.set_defaults(func=cmd_accept)

    sp = sub.add_parser("verify", help="check that scoped and full builds score identically")
    sp.add_argument("--ws")
    sp.add_argument("--styles", nargs="+", default=DEFAULT_STYLES)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("sweep", help="try every option of variant primes")
    sp.add_argument("--ws", required=True, help="never sweep in the main checkout")
    sp.add_argument("--keys", help="comma-separated prime keys (default: all touching the reference set)")
    sp.add_argument("--style", default="Regular", choices=STYLES)
    sp.add_argument("--section", default="design", help="variants section: design, upright, italic")
    sp.add_argument("--apply", action="store_true", help="keep the best option when it beats the current one")
    sp.add_argument("--min-gain", type=float, default=0.002)
    sp.add_argument("--shard", help="I/N: sweep only shard I (0-based) of N, balanced by option count")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("score", help="score glyphs against the reference")
    cand_args(sp)
    sp.add_argument("--out", help="write full JSON here")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--top", type=int, default=0, help="only print the N worst glyphs")
    sp.set_defaults(func=cmd_score)

    sp = sub.add_parser("diff", help="compare two score files (gate)")
    sp.add_argument("base")
    sp.add_argument("new")
    sp.add_argument("--target", help="characters being worked on; must improve")
    sp.add_argument("--tol", type=float, default=0.003, help="allowed drop for non-target glyphs")
    sp.add_argument("--show", type=float, default=0.002, help="print glyphs that moved at least this much")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("show", help="write overlay PNGs (black both, red reference only, blue candidate only)")
    cand_args(sp)
    sp.add_argument("--out", required=True)
    sp.add_argument("--scale", type=float, default=0.6)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("sheet", help="contact sheet of overlays")
    cand_args(sp)
    sp.add_argument("--out", required=True)
    sp.add_argument("--scores", help="score JSON to label tiles with")
    sp.add_argument("--scale", type=float, default=0.15)
    sp.add_argument("--cols", type=int, default=16)
    sp.set_defaults(func=cmd_sheet)

    sp = sub.add_parser("measure", help="vertical metrics, stems and side bearings")
    cand_args(sp)
    sp.set_defaults(func=cmd_measure)

    sp = sub.add_parser("ws-new", help="create per-agent workspaces wt/<name>")
    sp.add_argument("name")
    sp.add_argument("--rev", default="@-", help="committed base revision in both repos (default @-)")
    sp.add_argument("--no-base", action="store_true", help="skip building and scoring the base")
    sp.set_defaults(func=cmd_ws_new)

    sp = sub.add_parser("ws-rm", help="forget and delete wt/<name>")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_ws_rm)

    args = p.parse_args()
    global FULL
    FULL = args.full
    args.func(args)


if __name__ == "__main__":
    main()
