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
| Regular | printable ASCII, Latin-1, Latin Extended, punctuation, currency, arrows, math, box drawing (335 mapped) |
| Bold, Italic (Berkeley's Oblique), BoldItalic | `(),.5ABEFGIMNORTWabcdefghiklmnopqrstuvwxy` |
| Every other Ioskeley weight and width (`Thin` … `Black`, `SemiCondensed*`, their italics) | `A` and `a` from the datasheet's cut table (page 4) |

Commands default to the Regular style and to every reference glyph (`--glyphs all`).
Pass `--styles`/`--style` for the others.

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
| `match ws-new NAME` | jj workspaces of both repos at the committed `@-`, warm caches, build + score the base (~8 s) |
| `match check --ws NAME --target 'ab'` | build, score, gate against the workspace base (~2–3 s for Regular) |
| `match verify --ws NAME` | build scoped and full fonts and confirm every scored glyph matches (~10 s) |
| `match tune --ws NAME FIELD 'v1;v2' --apply` | try values for a global field in parallel worker copies (a `metricOverride` name, or a dotted table path such as `buildPlans.IoskeleyMono.slopes.Italic.angle`); negative values go after `--` |
| `match accept --ws NAME` | make the last check the new base after a passing step |
| `match show --ws NAME --glyphs 'ab' --out DIR` | large overlay PNGs: black both, red reference only, blue candidate only |
| `match sheet --ws NAME --out FILE.png` | contact sheet of every glyph with its score |
| `match measure --ws NAME` | heights, stems, side bearings vs reference |
| `match score --ws NAME --top 20` | worst glyphs first |
| `match sweep --ws NAME --keys a,g --apply` | try every Iosevka variant option for those primes |
| `match ws-rm NAME` | forget the workspaces and delete `wt/NAME`; uncommitted edits are kept as a commit |

### Build modes

By default `match` builds `scoped::` targets (`dist/IoskeleyMono/TTF-Scoped/`): the
code-point ranges in Iosevka's `verdafile.mjs` (`SCOPED_RANGES`: Latin, punctuation,
currency, arrows, math, box drawing) and only the glyph blocks they depend on, with
accented letters composed and no OpenType features. Missing from scoped builds:
`©®™Ĳĳ`, which come from Iosevka's derived-glyph modules. The first build after a glyph-code
or variant change runs everything once and records the dependency closure in
`.build/TTF-Scoped/`; later builds reuse it while the glyph code, the variants and the
slope kind are unchanged (numeric weight, width and slant do not invalidate it). If a
scoped run misses a recorded code point or fails, it falls back to a full run.

`match --full <command>` builds and scores the complete fonts (`single::` targets,
`TTF-Unhinted/`). Run `match verify` before integrating glyph-code changes.

### Where the time goes

Iosevka compiles each font in one single-threaded Node process. A full style takes 5–6 s
(about 30 s after a global change, which invalidates the geometry cache for every
glyph); a scoped style takes about 2 s. Styles build side by side in one verda session.
`tune` evaluates candidate values at the same time in worker copies
(`wt/.workers/<workspace>-N`: APFS clones without jj metadata, synced with rsync before
each run); `--jobs` sets how many.

## Agent loop (one glyph or glyph group)

1. `match ws-new <name>`; work only inside `~/ioskeley/wt/<name>/`.
2. `match show --ws <name> --glyphs '<chars>' --out ~/ioskeley/runs/<name>/img` and look at the
   overlay. Decide what differs: width, height, stroke weight, terminals, joins, curve shape.
3. Make one change: a variant in `IoskeleyMono/private-build-plans.toml`, or the glyph code
   under `Iosevka/packages/font-glyphs/src/`. Prefer parameters of the glyph's own block
   over shared helpers. If a shared helper must change, the gate checks the side effects.
4. `match check --ws <name> --target '<chars>'`. It passes when the targets' scores rise
   together (one may drop by up to `--tol` while the group gains) and no other glyph drops
   more than `--tol`.
   - PASS: look at `match show` again. Keep the change only if the glyph's structure
     matches Berkeley's: never add parts Berkeley's glyph lacks (serifs, spurs, tails,
     crossbars) or remove parts it has, even when the score rises. The score rewards
     overlapping ink, and a wrong part can fill area where a right part is missing.
     Then `match accept --ws <name>` and commit in each repo you changed
     (`jj commit -m "<glyph>: <what changed>"`).
   - FAIL: undo with `jj restore` and try something else. `show` and `score` rebuild first,
     so their output always matches the current files.
5. Repeat until the score stops improving. Report the final scores and the commit list.

Glyph code is often shared beyond the scored glyphs (for example Cyrillic letters reuse Latin
helpers). List any unscored glyphs a change also moves.

Glyphs that share drawing code (`()`, `[]`, `{}`, `<>`, `,;`, `:;`, `'"`, `mnhu`,
`bdpq`) belong in the same task.

If jj reports "The working copy is stale", stop. Do not run `jj workspace update-stale`:
it replaces uncommitted files with the rewritten commit's contents. Copy your changed
files out first. Workspaces go stale only when someone rewrites the commits they sit on:
while workspaces exist, do not `jj squash`, `jj describe` or rebase the commits they
started from, and integrate a workspace only after its agent has finished.

## Integrating a finished workspace

Run this for each repo (`Iosevka`, `IoskeleyMono`) the workspace changed:

```sh
cd ~/ioskeley/wt/<name>/<repo> && jj commit -m "<what changed>"   # leaves an empty working copy
match ws-rm <name>                                                 # clean, so nothing is lost
cd ~/ioskeley/<repo>
jj commit -m "<pending main-checkout work>"   # only if `jj st` shows changes
jj rebase -r '<first>::<last>' -d @-          # the workspace's commits, onto main's latest
jj new <last>                                 # continue on top of them
match verify && match check --rebase          # scoped = full, then record the new base
```

Conflicts in `private-build-plans.toml` between glyph tasks are usually separate
variant keys: keep both lines.
