---
name: animation-dsl
description: Reference for the Nixie display animation DSL — the `.ani` file format parsed by FileAnimation (pyxielib/animation_file.py) and its sandbox block parsed by SandboxParser (pyxielib/animation_sandbox.py). Covers the sprite/segment/frame/scale/sequence/repeat/flatten/import/sandbox commands, the content grammar, and the sandbox expression mini-language (assignment/set/print) with its +, *, | operators. Use when reading, writing, or debugging files in animations/ or those two parsers.
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
- `sequence|start|name` / `sequence|end` / `sequence|insert|name|shift` — define and insert named reusable frame sequences; `shift` (optional integer) slides the inserted sequence left/right
- `repeat|start|N` / `repeat|end` — anonymous sequence repeated N times inline; may appear inside a named sequence, but named sequences may not be started inside a repeat block
- `flatten|start|name` / `|content` lines / `flatten|end` — overlay anonymous inline segments per-tube (as hex bitmaps) into a named segment
- `import|[scale|]filepath` — import sprites/segments/sequences from a library file; `scale` is optional and multiplies imported sequence delays
- `sandbox|start` / `sandbox|end` — assemble animations from `animation_library.py` (see below); printed animations are appended to the file as full frames
- `{N}` in content is a multiplier; `{sprite_name}` expands a named sprite/segment; `{0x1A2B}` inserts a raw 16-bit bitmap

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

### Expressions

Tokenized, then evaluated in a second pass with precedence `*` then `+` then
`|`. They may contain:

- `animation_library` functions — the name is tried as-is, then with a `make` prefix (so `TextAnimation` resolves to `makeTextAnimation`); arguments are variables/literals/`name=value` kwargs only (no nested calls)
- variables defined earlier in the block
- int/float/string literals
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
