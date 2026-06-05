---
name: animation-dsl
description: Reference for the Nixie display animation DSL — the `.ani` file format parsed by FileAnimation (pyxielib/animation_file.py) and its sandbox block parsed by SandboxParser (pyxielib/animation_sandbox.py). Covers the sprite/segment/frame/scale/sequence/repeat/flatten/merge/import/sandbox commands, the content grammar, and the sandbox expression mini-language (assignment/set/print) with its +, *, | operators. Use when reading, writing, or debugging files in animations/ or those two parsers.
---

# Nixie Animation DSL (`.ani`)

Files in `animations/` use a custom line-based DSL parsed by `FileAnimation`
(`pyxielib/animation_file.py`). Each non-blank line is `command|arg1|arg2|...`;
`#` begins a comment (there is no `//` comment syntax). Errors are collected
per line and reported together with line numbers.

## Content grammar

```
literal    : Printable ASCII text (no `{` or `}`) — each character is one tube
sprite     : A named hexadecimal 16-bit bitmap
multiplier : A non-negative integer
macro      : `{name}` — expands a named sprite (one tube) or segment (N tubes)
rep        : `{multiplier}` — repeats the previous tube N times (`{0}` drops it)
content    : (literal | macro | rep)*
segment    : content
frame      : content
sequence   : frame+
comment    : `#` rest of line
```

### Macros and bitmaps

- **`{name}`** — expands a named sprite (one tube) or segment (N tubes).
- **`{N}`** — repeats the previous tube N times. `{0}` drops the previous tube
  entirely (the macro/tube is skipped).
- **`{0x1A2B}`** — inserts a raw 16-bit bitmap.
- **`~` prefix** — takes the bitwise-NOT (16-bit complement) of either form:
  - Inline hex literal: `{~0x0008}` → `0xFFF7`
  - Sprite definition value: `sprite|name|~0x0008`
  - Macro expansion: `{~tb_rail}` complements each tube of the sprite/segment, so a blank tube becomes all-on

The `~` prefix is handy with `mask` to clear specific segments:
`mask|{~0x0008}{16}` keeps everything *except* segment `0x0008`.

### Escaping `|` and `\`

`|` separates a line into fields, so a literal `|` inside a field (e.g. in
`frame`/`segment` content) must be escaped as `\|`. Because `\` is the escape
character, a literal backslash is written `\\`. Both escapes are collapsed when
the line is split into fields (`\|` → `|`, `\\` → `\`); any other backslash is
left untouched, so a lone `\` followed by a normal character still renders as a
backslash glyph. Example: `frame|1|A\|B` renders the three tubes `A`, `|`, `B`.
This applies only to DSL command line field-splitting (`FileAnimation._splitFields`)
— inside a `sandbox` block `|` is the tube-concatenation operator and a trailing
`\` is line continuation, neither of which is escaped.


## File commands

### Content primitives

**`sprite|name|0xHEX`** — define a named 16-bit bitmap.

**`segment|name|{sprite1}{sprite2}...`** — define a named sequence of characters and sprites.

**`frame|delay_secs|<content>`** — add a full frame composed of characters, sprites, and segments.
`delay=0` overlays the frame on the previous frame.

**`scale|factor`** — multiply all subsequent frame delays by `factor`.

**`import|[scale|]filepath`** — import sprites, segments, and sequences from a library file.
The optional `scale` prefix multiplies the delays of imported sequences.

---

### Sequences

**`sequence|start|name`** / **`sequence|end`** — define a named reusable frame sequence.

**`sequence|insert|name`** — insert a previously defined named sequence.
All options are **named arguments** (see [Named arguments](#named-arguments)):

- **`shift=N`** (integer, default `0`) — slides the sequence left/right along the tube axis.
- **`repeat=N`** (positive integer, default `1`) — inserts the sequence N times.
- **`scale=F`** (float, default the current `scale`) — multiplies each inserted frame's delay.
- **`mode=M`** — units for `start`/`end` slicing: `frame` (default, frame indices),
  `time` (raw pre-`scale` delays), or `scaled_time` (post-`scale` delays).
  Time-mode boundaries snap to the nearest frame boundary.
- **`start=B`** / **`end=B`** — select a sub-range of the sequence *before*
  `shift`/`scale`/`repeat` are applied. `start` is inclusive, `end` exclusive
  (like a Python slice). Defaults: `start` = sequence start, `end` = sequence end.
  Negative values measure from the end (e.g. `start=-2` in `frame` mode begins
  two frames before the end). A boundary's magnitude may not exceed the sequence's
  length (frame count in `frame` mode; total duration in the time modes).
- **`reverse=B`** (`true`/`false`, any case, default `false`) — when `true`,
  reverses the frame order before `shift`/`scale`/`repeat` are applied (after the
  `start`/`end` sub-range is selected).

**`sequence|anon`** — anonymous sequence block. Takes no name; accepts the same
`shift`/`repeat`/`scale`/`mode`/`start`/`end`/`reverse` arguments as `sequence|insert`.
Equivalent to defining a named sequence from the enclosed frames and immediately
inserting it at `sequence|end`; the sequence is not registered under a name.

**`overlay|<content>`** — only valid inside a named or anonymous sequence.
Like `frame` but with no delay: content is overlaid onto **every** frame of the
sequence at `sequence|end`. Blank overlay tubes leave the frame untouched;
overlapping content is merged as bitmaps (bitwise OR). More than one `overlay` is allowed.

**`mask|<content>`** — only valid inside a named or anonymous sequence.
Like `overlay` but content is bitwise-**ANDed** with every frame: only segments
lit in both the frame and the mask survive. A tube the mask does not reach
(mask shorter than the display, or a blank mask tube) contributes `0`, clearing
that tube — a mask must span every tube it means to keep (e.g. `mask|{keep}{16}`).
More than one `mask` is allowed.

**Ordering of `overlay` and `mask`:** Both are applied at `sequence|end` in the
order they appear in the sequence, independent of where they sit relative to
`frame` lines. A `mask` before an `overlay` clips the base frames but not the
overlay; a `mask` after an `overlay` clips both.

**`repeat|start|N`** / **`repeat|end`** — repeat the enclosed frames N times inline.
May appear inside a named sequence; named sequences may *not* be started inside
a repeat block.

---

### Flatten

**`flatten|start|name`** / content lines / **`flatten|end`** — overlay anonymous
inline segments per-tube (as hex bitmaps) into a named segment.

**`flatten|anon|scale`** / content lines / **`flatten|end`** — like `flatten|start`,
but instead of naming a segment it inserts the flattened result as a single full
frame right after the block, using `scale` as that frame's delay (multiplied by
the file `scale`).

---

### Merge

**`merge|start|name`** / body lines / **`merge|end`** — overlay whole sequences
per-step (each step's full frame is flattened tube-by-tube) into a named sequence.
Each body line names an existing sequence with optional named arguments:

- **`shift=N`** (integer, default `0`) — slides that sequence along the tube axis.
- **`repeat=N`** (positive integer, default `1`) — duplicates that sequence before merging.
- **`pad=N`** (non-negative integer, default `0`) — prepends N blank frames to the
  start of that sequence (after shift/repeat); delays are inferred from sequences
  processed before it (see pad resolution below).

The sequences must have the same *shape*: frames at each step must share the same
delay. Shorter sequences are padded with blank frames (taking the longer sequences'
delays) to match the longest length.

**Pad resolution:** Body lines are processed in increasing `pad` order. At least
one sequence must have `pad=0`. A sequence may not be padded past the end of every
already-processed sequence (its blank region must fit within a previously
processed sequence's length).

**`merge|anon`** — like `merge|start` but immediately inserts the merged result
rather than naming it. Accepts the same block-level
`shift`/`repeat`/`scale`/`mode`/`start`/`end` arguments as `sequence|anon`.
The per-line `shift`/`repeat`/`pad` still apply before merging; the block-level
`mode`/`start`/`end` slice the *merged result*.

---

### Collate

**`collate|start|name`** / body lines / **`collate|end`** — like `merge` with the
same subcommands and block-level `shift`/`repeat`/`scale`/`mode`/`start`/`end`
arguments, **but the sequences need not share a shape**. Where `merge` requires
frames at each step to share a delay, `collate` overlays sequences on a continuous
timeline: it unions every sequence's frame-end times into one ordered set of cut
points and emits one flattened full frame per interval. Use `collate` instead of
`merge` when the sequences have differing or arbitrary frame delays (e.g. staggered
copies). Body-line arguments unique to `collate`:

- **`delay=T`** (float ≥ 0 seconds, default `0`) — pushes that sequence's start
  T seconds down the timeline. `collate`'s analogue of `merge`'s `pad`, but in
  continuous time; there is no "at least one `delay=0`" requirement.
- **`scale=F`** (positive float, default `1`) — multiplies that one sequence's
  frame delays; applied before `shift`/`repeat`/`delay` and independent of the
  block-level `scale`.

**`collate|anon`** — like `merge|anon` but using collate semantics. Block-level
`mode`/`start`/`end` slice the collated result.

---

### Metadata

**`title|name`** — set the animation's display name for the **Animations** user
menu. `name` is a single alpha-numeric string. Allowed at most once per file.
When omitted (or left empty), the menu falls back to the file name without its
`.ani` extension. When several files resolve to the same name, the menu keeps the
first as-is and appends `(N)` to later repeats (`Clock`, `Clock(2)`, `Clock(3)`).

### Flow control

**`loop|once`** / **`loop|forever`** / **`loop|count|N`** — whether the animation
restarts after completing:

- `once` — play once (default when no `loop` command is present).
- `forever` — restart indefinitely.
- `count|N` — play N times total (`count|1` equals `once`).

At most one `loop` command is allowed; it must appear at the top level (not inside
any block) and is not permitted in library files.

**`sandbox|start`** / **`sandbox|end`** — assemble animations from
`animation_library.py` (see [Sandbox block](#sandbox-block) below). Printed
animations are appended to the file as full frames.

**`<type>|disable`** — disable a block without removing it. Every line through
the matching `<type>|end` is skipped unparsed (so even broken content inside is
ignored), and all arguments on the `disable` line itself are ignored. Change a
block's `start`/`anon` opener to `disable` to comment it out while leaving its
arguments and closing `<type>|end` intact. Supported types: `sequence`,
`flatten`, `sandbox`, `collate`.

---

## Named arguments

Some commands accept **named arguments** written `name=value` (e.g.
`sequence|insert|s|shift=2|repeat=3`). This is a general mechanism declared per
command via `ArgSpec` in `animation_file.py` and resolved by
`FileAnimation._bindArgs`. The rules:

- Positional arguments must come before any named argument in a call.
- Named arguments are always written `name=value`; they may never be passed
  positionally, and a command's positional argument may never be named.
- Named arguments may appear in any order, and each may appear at most once.
- A command only parses `name=value` fields if it declares named arguments, so
  commands like `frame` may still carry an `=` in their content.

---

## Sandbox block (`pyxielib/animation_sandbox.py`)

Between `sandbox|start` and `sandbox|end`, lines use a safe expression
mini-language (handled entirely by `SandboxParser`, never `eval`). Three line
types:

- **assignment** `name = expr` — `name` matches `[A-Za-z]\w+` (2+ chars) and
  may not be a DSL keyword, a class in `animation.py`, or `set`/`print`. The
  result must be an `animation.py` instance (or a same-typed list of them) and
  is stored in a namespace separate from the file's sprites/segments.
- **set** `set delay|rate = literal` — `delay` (defaults to the file `scale`)
  and `rate` are non-negative floats and mutually exclusive: setting one non-zero
  zeroes the other.
- **print** `print expr` — evaluates `expr`, converts the result to a
  `FullFrameAnimation`/`TubeAnimation` (a `Frame`/`FullFrame`/`List[FullFrame]`/
  `TubeSequence`/`List[TubeSequence]` is wrapped using `delay`/`rate`) and appends
  it to the file. With no argument (`print`), the most recently assigned variable
  is printed. `TubeAnimation`s are merged onto a shared timeline of full frames.

A sandbox line ending in `\` is joined with the following line before parsing
(line continuation). A `\` with no following line before `sandbox|end` is an error.

### Expressions

Tokenized, then evaluated in a second pass with precedence `*` then `+` then `|`.
They may contain:

- `animation_library` functions — the name is tried as-is, then with a `make`
  prefix (`TextAnimation` resolves to `makeTextAnimation`); arguments are
  variables/literals/`name=value` kwargs only (no nested calls).
- Variables defined earlier in the block.
- int/float/string literals, plus `True`/`False`/`None` (valid as argument values
  only, in that exact capitalization).
- `[...]` lists of same-typed items.
- The operators `+`, `*`, `|`.

A bare (non-argument) string literal is converted to a `FullFrame` via `textToFrames`.

### The `|` (tube-concatenation) operator

`|` joins operands side-by-side along the tube axis; both sides must be the same type:

| Left | Right | Result |
|------|-------|--------|
| `Frame` | `Frame` | `FullFrame` |
| `TubeSequence` | `TubeSequence` | `List[FullFrame]` |
| `TubeAnimation` | `TubeAnimation` | `FullFrameAnimation` (merged onto a shared timeline; ragged tube counts are blank-padded) |

This is backed by real `__or__` operators on the `animation.py` classes (plus
`TubeAnimation.toFullFrameAnimation()` and the `concatFullFrameRows` /
`concatFullFrameTimelines` module helpers), so `|` works in plain Python too;
the sandbox just delegates to it (and joins bare `List[FullFrame]` rows itself,
since plain lists can't carry an operator).
