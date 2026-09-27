# tui

A full-screen terminal UI toolkit in Coil: raw mode and signal-safe terminal
restoration, a pure key decoder, a width-aware cell canvas with diff rendering,
and a set of widgets (lists, tables, text input, chat-style scroll views,
spinners, badges, progress bars). It has no dependency on the factory app or on
factory code.

```toml
[dependencies]
tui = { path = "../coil-factory/libraries/tui" }
```

Try the demo (a mock factory dashboard with a list, a table, a chat transcript
and an input box):

```sh
cd examples/demo && coil build && ./build/release/tui-demo
./build/release/tui-demo --snapshot   # one frame as plain text, no terminal needed
```

`TUI_COLOR=256` (or `16`, `none`, `truecolor`) forces a color mode, which is
handy for checking the fallbacks.

## Model

Each frame, the application draws its whole UI into a `Canvas`, a grid of
styled cells. `render-diff!` compares it with the previous frame and emits only
the cells that changed: cursor moves (CUP), minimal SGR transitions, and the
whole frame wrapped in synchronized-output mode so it never flickers. The frame
is written with a single `write`. `tui.app/app-run` runs this loop for you.

Everything is measured in terminal cells. Text is segmented into grapheme
clusters (UAX #29). Wide CJK and emoji take 2 cells; combining marks, ZWJ and
variation selectors take 0.

Failure policy: states the library cannot handle (such as a table whose cell
count is not a multiple of its column count) abort through `tui.fatal/fatal`.
That call restores the terminal first and then prints `tui: fatal: …`. Nothing
falls back silently to a default.

## `tui.width`: text measurement

| Function | |
| --- | --- |
| `(text-width text)` | display width of UTF-8 bytes |
| `(rune-width rune)` | width of one scalar: 0 / 1 / 2 |
| `(truncate-to-width a text max ellipsis?)` | cut to `max` cells, optionally ending in `…`; borrows `text` unless an ellipsis is added |
| `(pad-to-width a text w)` / `(pad-left-to-width a text w)` | truncate with `…`, then pad with spaces |
| `(prefix-within-width text max)` | `WidthPrefix {bytes width}` of the longest prefix that fits |
| `(clusters text)` + `(cluster-next! (mut it))` | iterate `Cluster {start end width rune single}` |
| `(utf8-decode bytes at)` | `Utf8Decoded {rune len valid}`; malformed input gives U+FFFD, len 1 |
| `(utf8-encode! (mut list) rune)`, `(utf8-encode-into ptr rune)`, `(utf8-sequence-length lead)` | |

```coil
(text-width "中文 ok")                       ; 7
(truncate-to-width scratch "hello world" 5 true) ; "hell…"
```

## `tui.term`: the terminal

| Function | |
| --- | --- |
| `(term-enter! (term-options-default))` | raw mode, alternate screen, hidden cursor, bracketed paste, autowrap off → `(Result i64 TermError)` |
| `(term-leave!)` | restore everything (idempotent) |
| `(term-size)` | `TermSize {width height}` via TIOCGWINSZ, then `COLUMNS`/`LINES`, then 80×24 |
| `(detect-color-mode)` | `ColorMode`: `NO_COLOR` → `NoColor`; `COLORTERM=truecolor/24bit` → `TrueColor`; `TERM=*256color*` → `Ansi256`; otherwise `Ansi16`; `TERM` unset or `dumb` → `NoColor`; the `TUI_COLOR` variable overrides |
| `(color-mode-from-env no-color colorterm term override)` | the same decision as a pure function |
| `(term-write! bytes)` / `(write-fd fd bytes)` | write everything, retrying on EINTR |
| `(stdin-tty?)`, `(stdout-tty?)`, `(fd-tty? fd)` | |
| `(term-suspend!)` | Ctrl-Z: restore, SIGTSTP, then take over again on resume |
| `(term-wake-fd)`, `(term-drain-wake!)` | a pipe that becomes readable on SIGWINCH (used by the key reader) |

While the terminal is active, SIGHUP, SIGINT, SIGQUIT, SIGABRT, SIGSEGV, SIGBUS
and SIGTERM restore the terminal. The handler then reinstates the previous
disposition and re-raises the signal, so the process still dies with the right
status. A `tui.fatal` abort also restores the terminal. Raw mode disables signal
keys, so Ctrl-C arrives as the key `(Ctrl #\c)`.

## `tui.keys`: input

```coil
(defsum Key (Char [rune]) (Enter) (Tab) (BackTab) (Backspace) (Delete) (Insert)
  (Escape) (Up) (Down) (Left) (Right) (Home) (End) (PageUp) (PageDown)
  (Function [n]) (Ctrl [letter]) (Alt [rune]) (AltEnter) (ShiftEnter)
  (AltBackspace) (WordLeft) (WordRight) (Paste [text]) (Resize [width height])
  (NoKey) (Eof) (Unknown))
```

- `(decode-key bytes final)` → `(Decoded key consumed)` or `(Incomplete)`. It is
  pure, so it can be unit tested. It handles CSI and SS3 sequences, xterm
  modifiers (Alt/Ctrl+←/→ become `WordLeft`/`WordRight`), function keys,
  kitty/modifyOtherKeys Shift/Alt-Enter, bracketed paste, ESC-prefixed Alt keys
  and multi-byte UTF-8. When `final` is false, a partial sequence or a lone ESC
  returns `Incomplete`. When `final` is true (the escape timeout has passed), it
  resolves to what was typed, so a lone ESC becomes `Escape`.
- `(key-reader-new a)` + `(read-key! (mut r) timeout-ms)` block for up to
  `timeout-ms` (negative means forever) and return `Resize` as soon as the size
  changes. `Paste` text is valid until the next `read-key!`.

## `tui.canvas`: cells, styles, layout and rendering

Colors and styles:

```coil
(defsum Color (Default) (Rgb [r g b]) (Indexed [n]))
(defstruct Style [(fg Color) (bg Color) (attrs i64)])  ; ATTR-BOLD DIM ITALIC UNDERLINE REVERSE
(bold (fg (hex 0x7AA2F7)))         ; helpers: style-default fg with-fg with-bg with-attrs
                                   ; bold dim italic underline reverse, rgb hex indexed
```

Rects and layout: `(rect x y w h)`, `rect-inset`, `rect-inset-xy`,
`rect-take-top/-bottom/-left/-right`, `rect-drop-…`, `rect-intersect`,
`rect-center`, `rect-contains?`, `rect-right`, `rect-bottom`, `rect-empty?`.
Splits take `Constraint`s, which are `(Fixed n)`, `(Ratio num den)` or
`(Fill weight)`:

```coil
(let [(mut rows) (zeroed (array Rect 3))
      (mut cols) (zeroed (array Rect 2))]
  (split-vertical screen [(Fixed 1) (Fill 1) (Fixed 1)] 0 rows)   ; stacked, gap 0
  (split-horizontal (index rows 1) [(Fixed 30) (Fill 1)] 1 cols)) ; side by side, gap 1
```

Drawing:

| Function | |
| --- | --- |
| `(canvas-new a w h)`, `canvas-resize!`, `canvas-clear!`, `canvas-free!`, `canvas-rect` | |
| `(put-text! c clip x y text style)` → columns | one line, clipped, grapheme and width aware; returns the columns written so spans chain |
| `(put-text-ellipsis! …)` | same, but ends in `…` when cut |
| `fill!`, `restyle!`, `hline!`, `vline!`, `put-rune!` | |
| `(draw-box! c rect opts)` → inner rect | `BoxOpts {border style title title-style footer footer-style}`; `BorderKind` is `Rounded` (╭╮╰╯), `Plain`, `Heavy` or `Double`. `box-opts` and `box-titled` build the options |
| `(draw-box-divider! c rect y kind style)` | a ├──┤ divider inside a box |
| `canvas-set-cursor!`, `canvas-hide-cursor!` | where the terminal cursor goes after the frame |

Rendering:

| Function | |
| --- | --- |
| `(render-diff! (mut out) prev next mode)` | appends escapes that turn `prev` into `next`, or nothing if they are the same. An invalid or resized `prev` gets a full repaint |
| `(canvas-commit! prev next)` | record `next` as what is now on screen |
| `(render-full! (mut out) next mode)` | clear the screen and paint everything |
| `(render-plain a c)` | rows as plain text, right-trimmed and newline-terminated, for snapshot tests |
| `style-for-mode`, `color-for-mode`, `rgb-to-256`, `rgb-to-16` | truecolor downgrade. In `NoColor` mode a background tint becomes reverse video, so selections stay visible |

## `tui.widgets`

- **Theme:** `(theme-default)` is a dark palette that paints no background of
  its own. It uses blue/violet accents, green/amber/red status colors and muted
  blue-gray chrome. Fields: `text muted subtle accent accent-alt info success
  warning error border border-focus title title-focus header selection
  badge-fg`. `(panel-opts theme title focused?)` builds a rounded panel.
- **Wrapping:** `(wrap-text a text width)` returns an `(ArrayList (slice u8))` of
  lines that borrow `text`. It word-wraps, breaks long words hard, and keeps
  explicit newlines.
- **Lists:** `ListView {selected offset count}`. Functions:
  `list-handle-key!` (Up/Down, Ctrl-P/N, PageUp/PageDown, Home/End),
  `list-select!`, `list-move!`, `list-scroll-into-view!`, and
  `(draw-list! c r (mut lv) items theme focused)`.
- **Tables:** `(draw-table! c r columns cells (mut lv) gap theme focused)`.
  `Column {title width:Constraint align}`, where `Align` is `AlignLeft`,
  `AlignRight` or `AlignCenter`. Cells are a flat row-major `(slice TableCell)`
  built with `(table-cell text style)`. Cells truncate with `…`, the header
  uses the header style, and the selected row gets the selection tint while
  keeping each cell's color. A scrollbar appears when rows overflow.
- **Text input:** `(text-input-new a multiline?)` creates a `TextInput`.
  `(input-handle-key! (mut in) key)` returns `InputSubmitted`, `InputHandled`
  or `InputIgnored`. It covers characters, paste, Backspace/Delete, ←/→,
  Home/End, Ctrl-A/E/B/F, word motion (Alt-B/F, Alt/Ctrl-←/→), Ctrl-U, Ctrl-K,
  Ctrl-W, Alt-Backspace, Alt-D, and ↑/↓ (line motion in multi-line input,
  history otherwise). Shift-Enter, Alt-Enter and Ctrl-J insert a newline in
  multi-line mode. Related: `input-text`, `input-submit!` (copies the text,
  pushes it to history and clears), `input-set-text!`, `input-clear!`,
  `input-visual-rows`. `(draw-input! c r (mut in) style placeholder
  placeholder-style focused)` soft-wraps, scrolls to the cursor and places the
  terminal cursor.
- **Scroll view** (chat transcripts): `StyledLine {gutter gutter-style text
  style}`. `(push-wrapped! (mut lines) a text width style "│ " gutter-style)`
  appends wrapped lines. `ScrollView` sticks to the bottom until the user
  scrolls up, and scrolling back to the end re-arms it. Functions:
  `draw-scroll-view!`, `scroll-up!`, `scroll-down!`, `scroll-to-bottom!`,
  `scroll-view-handle-key!` (PageUp/PageDown).
- **Small pieces:** `(spinner-frame tick)` returns a braille spinner frame.
  `(draw-badge! c clip x y " RUNNING " (badge-style theme color))` draws a
  badge. `(draw-progress-bar! c r done total filled track)` draws a thin ━━╸
  bar at half-cell resolution. `draw-sparkline!` draws ▁…█.
  `draw-scrollbar!` draws a scrollbar. `(draw-key-hints! c r [(KeyHint :key "q"
  :label "quit")] theme)` draws a hint line.

## `tui.app`: the event loop

```coil
(defstruct Dash [(theme Theme) (runs ListView) (tick i64)])

(impl App Dash
  (app-draw [(self (ptr Dash)) (frame (ptr Frame))] (-> i64)
    (let [c (.canvas frame)                     ; cleared before every draw
          inner (draw-box! c (canvas-rect c) (panel-opts (.theme self) "Runs" true))]
      (put-text! c inner (.x inner) (.y inner) (spinner-frame (.tick frame)) (.accent (.theme self)))
      0))
  (app-event [(self (ptr Dash)) (event Event)] (-> AppFlow)
    (match event
      (KeyEvent [key] (match key (Escape [] (Quit)) (_ (Continue))))
      (TickEvent [n] (Continue))                 ; poll your data here
      (ResizeEvent [w h] (Continue)))))

(let [dash (primitive/alloc-stack Dash)]
  (set! dash (Dash :theme (theme-default) :runs (list-view-new) :tick 0))
  (app-run dash (app-options-default) (malloc-allocator)))   ; -> (Result i64 TermError)
```

`app-run` redraws after every event. `Frame` carries `canvas`, `scratch` (an
arena reset every frame, meant for per-frame strings and wrapped lines), `tick`
and `color-mode`. `AppOptions {tick-ms term color-mode suspend-on-ctrl-z}`
defaults to 100 ms ticks. The terminal is restored on every exit path. Without
a TTY, `app-run` returns `(Err (NotATerminal))` and leaves the screen alone.
`(app-snapshot app w h tick a)` draws one frame to plain text for whole-screen
snapshot tests.

## Tests and checks

```sh
coil test          # 40 tests: decoder, reader over a pipe, widths, wrapping,
                   # canvas clipping, boxes, exact render-diff bytes, tables,
                   # input editing, scroll view, app snapshot
coil lint && coil fmt --check
```

## Known compiler issues worked around

- `get` on a `(slice (array i64 2))` fails LLVM verification ("Function return
  type does not match operand type of return inst"). This is coil-bugs
  `cu7ae3pg3f9`. `tui.width` reads its range tables through a flat `(ptr i64)`
  view instead.
- A `(mut x)` argument to a call nested inside a named struct constructor is
  rejected. This is coil-bugs `cj5wtgd2jty`. Bind the call's result with `let`
  first.
