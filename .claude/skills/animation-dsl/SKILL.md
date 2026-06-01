---
name: animation-dsl
description: Reference for the Nixie display animation DSL — the `.ani` file format parsed by FileAnimation (pyxielib/animation_file.py) and its sandbox block parsed by SandboxParser (pyxielib/animation_sandbox.py). Covers the sprite/segment/frame/scale/sequence/repeat/flatten/merge/import/sandbox commands, the content grammar, and the sandbox expression mini-language (assignment/set/print) with its +, *, | operators. Use when reading, writing, or debugging files in animations/ or those two parsers.
---

# Nixie Animation DSL (`.ani`)

Files in `animations/` use a custom line-based DSL parsed by `FileAnimation`
(`pyxielib/animation_file.py`). Each non-blank line is `command|arg1|arg2|...`;
`#` begins a comment (there is no `//` comment syntax). Errors are collected
per line and reported together with line numbers.

## File commands

- `sprite|name|0xHEX` — define a named 16-bit bitmap
- `segment|name|{sprite1}{sprite2}...` — define a named sequence of characters and sprites
- `frame|delay_secs|<content>` — add a full frame composed of characters, sprites, and segments; `delay=0` overlays on the previous frame
- `scale|factor` — multiply all delays
- `sequence|start|name` / `sequence|end` / `sequence|insert|name[|shift=N][|repeat=N][|scale=F]` — define and insert named reusable frame sequences. The insert options are **named arguments** (see below): `shift` (integer, default `0`) slides the inserted sequence left/right; `repeat` (positive integer, default `1`) inserts the sequence that many times; `scale` (float, default the current `scale`) multiplies each inserted frame's delay
- `sequence|anon[|shift=N][|repeat=N][|scale=F]` / frames / `sequence|end` — an anonymous sequence block. Takes no name and accepts the same `shift`/`repeat`/`scale` arguments as `sequence|insert`. Equivalent to defining a named sequence from the enclosed frames and immediately inserting it with those arguments at `sequence|end`; the sequence is not registered under a name
- `repeat|start|N` / `repeat|end` — anonymous sequence repeated N times inline; may appear inside a named sequence, but named sequences may not be started inside a repeat block
- `flatten|start|name` / `|content` lines / `flatten|end` — overlay anonymous inline segments per-tube (as hex bitmaps) into a named segment
- `flatten|anon|scale` / `|content` lines / `flatten|end` — like `flatten|start`, but instead of naming a segment it inserts the flattened result as a single full frame into the animation right after the block, using `scale` as that frame's delay; like a `frame` delay it is multiplied by the file `scale`
- `merge|start|name` / `|seq[|shift=N]` lines / `merge|end` — overlay whole sequences per-step (each step's full frame is flattened tube-by-tube like `flatten`) into a named sequence. Each body line names an existing sequence and may carry a `shift` named argument (integer, default `0`) that slides that sequence along the tube axis before merging, behaving like `sequence|insert`'s `shift`. The sequences must have the same shape — the frames at a given step must share the same delay — and shorter sequences are padded with blank frames (which take the longer sequences' delays) out to the longest length
- `merge|anon[|shift=N][|repeat=N][|scale=F]` / `|seq[|shift=N]` lines / `merge|end` — like `merge|start`, but instead of naming a sequence it immediately inserts the merged sequence into the animation right after the block, accepting the same `shift`/`repeat`/`scale` insert arguments as `sequence|anon` (the per-line `shift` still applies before merging, independent of the block-level `shift` applied to the merged result)
- `import|[scale|]filepath` — import sprites/segments/sequences from a library file; `scale` is optional and multiplies imported sequence delays
- `sandbox|start` / `sandbox|end` — assemble animations from `animation_library.py` (see below); printed animations are appended to the file as full frames
- `sequence|disable` / `flatten|disable` / `sandbox|disable` — disable a block: every line through the matching `<type>|end` is skipped unparsed (so even broken content inside is ignored), and all arguments on the `disable` line itself are ignored. Lets you comment out a whole block by changing its `start`/`anon` opener to `disable` without removing its arguments (the closing `<type>|end` stays)
- `{N}` in content is a multiplier; `{sprite_name}` expands a named sprite/segment; `{0x1A2B}` inserts a raw 16-bit bitmap

## Named arguments

Some commands accept **named arguments** written `name=value` (e.g.
`sequence|insert|s|shift=2|repeat=3`). This is a general mechanism declared per
command via `ArgSpec` in `animation_file.py` and resolved by
`FileAnimation._bindArgs`, so it can be extended to other commands. The rules:

- Positional arguments must come before any named argument in a call.
- Named arguments are always written `name=value`; they may never be passed
  positionally, and a command's positional argument may never be named.
- Named arguments may appear in any order, and each may appear at most once.
- A command only parses `name=value` fields if it declares named arguments, so
  commands like `frame` may still carry an `=` in their content.

## Content grammar

```
literal    : Printable ASCII text (no `{` or `}`) — each character is one tube
sprite     : A named hexadecimal 16-bit bitmap
multiplier : A positive integer
macro      : `{name}` — expands a named sprite (one tube) or segment (N tubes)
rep        : `{multiplier}` — repeats the previous tube N times
content    : (literal | macro | rep)*
segment    : content
frame      : content
sequence   : frame+
comment    : `#` rest of line
```

## Sandbox block (`pyxielib/animation_sandbox.py`)

Between `sandbox|start` and `sandbox|end`, lines use a safe expression
mini-language (handled entirely by `SandboxParser`, never `eval`). Three line
types:

- **assignment** `name = expr` — `name` matches `[A-Za-z]\w+` (2+ chars) and may not be a DSL keyword, a class in `animation.py`, or `set`/`print`. The result must be an `animation.py` instance (or a same-typed list of them) and is stored in a namespace separate from the file's sprites/segments.
- **set** `set delay|rate = literal` — `delay` (defaults to the file `scale`) and `rate` are non-negative floats and mutually exclusive: setting one non-zero zeroes the other.
- **print** `print expr` — evaluates `expr` (any expression, not just a variable), converts the result to a `FullFrameAnimation`/`TubeAnimation` (a `Frame`/`FullFrame`/`List[FullFrame]`/`TubeSequence`/`List[TubeSequence]` is wrapped using `delay`/`rate`) and appends it to the file. With no argument (`print`), the most recently assigned variable is printed. `TubeAnimation`s are merged onto a shared timeline of full frames.

A sandbox line ending in `\` is joined with the following line before parsing, so a long expression can be split across several lines. A `\` with no following line before `sandbox|end` is an error.

### Expressions

Tokenized, then evaluated in a second pass with precedence `*` then `+` then
`|`. They may contain:

- `animation_library` functions — the name is tried as-is, then with a `make` prefix (so `TextAnimation` resolves to `makeTextAnimation`); arguments are variables/literals/`name=value` kwargs only (no nested calls)
- variables defined earlier in the block
- int/float/string literals, plus `True`/`False`/`None` (only valid as argument values, and only in that exact capitalization)
- `[...]` lists of same-typed items
- the operators `+`, `*`, `|`

A bare (non-argument) string literal is converted to a `FullFrame` via
`textToFrames`.

### The `|` (tube-concatenation) operator

`|` joins operands side-by-side along the tube axis; both sides must be the
same shape:

- `Frame | Frame → FullFrame`
- `TubeSequence | TubeSequence → List[FullFrame]`
- `TubeAnimation | TubeAnimation → FullFrameAnimation` (timed operands merged onto a shared timeline; ragged tube counts are blank-padded)

This is backed by real `__or__` operators on the `animation.py` classes (plus
`TubeAnimation.toFullFrameAnimation()` and the `concatFullFrameRows` /
`concatFullFrameTimelines` module helpers), so `|` works in plain Python too;
the sandbox just delegates to it (and joins bare `List[FullFrame]` rows itself,
since plain lists can't carry an operator).
