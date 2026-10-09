# match: hill-climbing Ioskeley Mono toward Berkeley Mono

`match` builds single Ioskeley styles from a forked Iosevka checkout, renders each
glyph over the matching Berkeley Mono (TX-02) glyph, and scores how well they overlap.
A change is kept only when the glyphs it targets improve and no other glyph gets worse.

## Rules

- The reference outlines come from the fonts embedded in the TX-02 datasheet PDF. They are
  proprietary. Use them only for measurements and overlays on your machine.
  Never commit the PDF, the extracted fonts, rendered reference images, or outline
  coordinates copied from them. Change Iosevka's parametric glyph code and build plan
  parameters until the overlay agrees.
- `metricOverride` appears once per build plan in `private-build-plans.toml` (Iosevka
  cannot inherit it). All copies must stay identical; `match build` refuses to build otherwise.

## Layout

```
~/ioskeley/                 (override with IOSKELEY_ROOT)
  TX-02-datasheet.pdf       https://usgraphics.com/products/berkeley-mono → Datasheet
  reference/TX02-*.otf      extracted by `match ref`
  Iosevka/                  fork of be5invis/Iosevka (branch based on v34.4.0)
  IoskeleyMono/             fork of ahatem/IoskeleyMono
  wt/<name>/{Iosevka,IoskeleyMono}   per-agent jj workspaces
  runs/<name|main>/         base/, last/, sweep/ score JSON
```

`Iosevka/private-build-plans.toml` is a symlink to the sibling `IoskeleyMono` copy, so
each workspace builds its own plan.

## Setup

```sh
mkdir ~/ioskeley && cd ~/ioskeley
curl -sSLo TX-02-datasheet.pdf https://usgraphics.com/static/products/TX-02/datasheet/TX-02-datasheet.a43c0c7f8d8c.pdf
jj git clone --colocate git@github.com:<you>/IoskeleyMono.git
jj git clone --colocate git@github.com:<you>/Iosevka.git   # then check out the Ioskeley branch
(cd Iosevka && npm install && ln -s ../IoskeleyMono/private-build-plans.toml .)
alias match=~/ioskeley/IoskeleyMono/tools/match/match     # uv runs it with its own deps
match ref                    # extract reference fonts
match check --rebase         # build the four styles, record runs/main/base
```

## Reference coverage

| Style | Reference glyphs |
|---|---|
| Regular | all printable ASCII plus Latin-1/Latin Extended, arrows, box drawing (335 mapped) |
| Bold, Italic (Berkeley's Oblique), BoldItalic | `(),.5ABEFGIMNORTWabcdefghiklmnopqrstuvwxy` |

## Score

Each glyph is rendered on the same 800×1350-unit canvas (4 units per pixel, same origin,
both fonts have 1000 units per em and a 600-unit advance) with no alignment step, so
position, size, and shape all count.

- `iou`: overlap of the two coverage images (sum of min / sum of max).
- `blur_iou`: the same after a 12-unit Gaussian blur; it still gives a signal
  when strokes are close but not yet overlapping.
- `score` = mean of the two. 1.0 is identical.
- `ink_ratio` > 1 means the candidate is heavier; `bbox_delta` is candidate minus
  reference for (xMin, yMin, xMax, yMax) in font units.

`check` gates per style: every `--target` character must improve, and no other glyph may
drop more than `--tol` (default 0.003). Without `--target`, the total must improve.

## Commands

| Command | Use |
|---|---|
| `match ws-new NAME` | jj workspaces of both repos at `@`, warm caches, build + score the base (~8 s) |
| `match check --ws NAME --target 'ab'` | build all four styles in parallel, score, gate against the workspace base (~5 s) |
| `match accept --ws NAME` | make the last check the new base after a passing step |
| `match show --ws NAME --glyphs 'ab' --out DIR` | large overlay PNGs: black both, red reference only, blue candidate only |
| `match sheet --ws NAME --out FILE.png` | contact sheet of every glyph with its score |
| `match measure --ws NAME` | heights, stems, side bearings vs reference |
| `match score --ws NAME --top 20` | worst glyphs first |
| `match sweep --ws NAME --keys a,g --apply` | try every Iosevka variant option for those primes |
| `match ws-rm NAME` | forget the workspaces and delete `wt/NAME`; uncommitted edits are kept as a commit |

### Build modes

By default `match` builds `fast::` targets (`dist/IoskeleyMono/TTF-Fast/`). They skip
Iosevka's derived glyphs (math-styled letters, enclosures, superscripts, `©®™Ĳĳ`), which
halves the compile. ASCII and Latin letters are built from the same code, and fast
and full builds score the same on them. `match --full <command>` builds and scores
the complete fonts (`single::` targets, `TTF-Unhinted/`); use it before integrating.

### Where the time goes

Iosevka compiles each font in one single-threaded Node process; one style takes about
3 s fast or 5–6 s full, mostly evaluating every glyph's code and solving spiro curves.
The geometry cache in `.build/cache` skips the outline boolean operations for glyphs
that did not change. Parallelism comes from building styles side by side (one verda
session) and from separate workspaces.

## Agent loop (one glyph or glyph group)

1. `match ws-new <name>`; work only inside `~/ioskeley/wt/<name>/`.
2. `match show --ws <name> --glyphs '<chars>' --out ~/ioskeley/runs/<name>/img` and look at the
   overlay. Decide what differs: width, height, stroke weight, terminals, joins, curve shape.
3. Make one change: a variant in `IoskeleyMono/private-build-plans.toml`, or the glyph code
   under `Iosevka/packages/font-glyphs/src/`. Prefer parameters of the glyph's own block
   over shared helpers. If a shared helper must change, the gate checks the side effects.
4. `match check --ws <name> --target '<chars>'`.
   - PASS: `match accept --ws <name>`, then commit in each repo you changed
     (`jj commit -m "<glyph>: <what changed>"`).
   - FAIL: undo with `jj restore` and try something else.
5. Repeat until the score stops improving. Report the final scores and the commit list.

Glyphs that share drawing code (`()`, `[]`, `{}`, `<>`, `,;`, `:;`, `'"`, `mnhu`,
`bdpq`) belong in the same task.

## Integrating parallel work

Each workspace's commits sit on the revision it started from. From the main checkouts:

```sh
jj rebase -s 'roots(<base>..<name>@-)' -d @-   # per workspace, in each repo
match check --target ''                       # confirm the combined result, then accept
match ws-rm <name>
```

Resolve conflicts in `private-build-plans.toml` by keeping both lines; separate glyph
tasks touch separate variant keys.
