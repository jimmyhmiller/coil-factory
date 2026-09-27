# cli

Building blocks for polished command-line programs in Coil: declarative
subcommands and flags with helpful errors, terminal styling that degrades
gracefully, aligned tables, humanized numbers, and line-oriented prompts.

```toml
[dependencies]
cli = { path = "../coil-factory/libraries/cli" }
```

| Namespace | What |
| --- | --- |
| `cli.args` | Commands, flags, `parse`, help and error rendering |
| `cli.style` | Color capability, `paint`, semantic colors, glyphs, display width, tables, relative times, sizes, counts, plurals |
| `cli.prompt` | `ask`, `ask-multiline`, `choose`, `confirm`, `read-stdin-all` |

Functions returning `(slice u8)` allocate an exactly sized result from the
allocator they are given (free with `(alloc/free a s)`, or use an arena for a
command's whole run). Programming errors — a table row with the wrong number of
cells, asking for a flag the command never declared — abort with a `cli: ...`
message rather than producing plausible wrong output.

A complete example lives in [`examples/greet`](examples/greet): `coil build`
there, then try `greet`, `greet hello --help`, `greet helo`, `greet status`,
and `greet interview`.

## A CLI with two subcommands

```coil
(module tool.main)

(import "coil.alloc" :as alloc :use *)
(import "coil.io" :as io)
(import "cli.args" :use *)
(import "cli.style" :use *)

(defn main [(argc i32) (argv (ptr (ptr i8)))] (-> i64)
  (let [a (malloc-allocator)
        ; Build the table in main's frame: its array literals are frame storage.
        prog (Program :name "tool"
                      :summary "Manage sites."
                      :version "0.1.0"
                      :commands [(Command :name "site add"
                                          :group "Sites"
                                          :summary "Add a remote site"
                                          :usage ""
                                          :args ["NAME"]
                                          :flags [(flag "ssh" 0 "HOST" "SSH destination")
                                                  (switch "sandbox" 0 "Sandbox shell commands")])
                                 (Command :name "ps"
                                          :group "Runs"
                                          :summary "List runs"
                                          :usage ""
                                          :args (no-args)
                                          :flags (no-flags))]
                      :flags [(switch "json" 0 "Print JSON")])
        t (term-stdout)]
    (match (parse-standard a prog (argv-from-main a argc argv))
      (Err [code] code)                       ; help/version printed (0) or error shown (2)
      (Ok [inv]
          (cond (command-is? inv "site add")
                  (do
                    (io/print-str (io/stdout) (ok a t (glyph-check t)))
                    (io/print-str (io/stdout) " added ")
                    (io/print-str (io/stdout) (accent a t (flag-value-or inv "ssh" "?")))
                    (io/print-str (io/stdout) "\n")
                    0)
                :else
                  (let [(mut table) (table-new a ["" "RUN" "AGE"])]
                    (table-align! (mut table) 2 (AlignRight))
                    (table-row! (mut table) [(ok a t (glyph-dot t)) "7f3a" (relative-time a 12000)])
                    (io/print-str (io/stdout) (table-render a t table))
                    0))))))
```

`tool strat` prints

```
error: unknown command 'strat' for 'tool'
...
```

and `tool site ad x` suggests `'tool site add'`.

## cli.args

### Declaring

```coil
(defstruct Flag [(long (slice u8)) (short u8) (takes-value bool) (value-name (slice u8)) (help (slice u8))])
(defn flag   [(long (slice u8)) (short i64) (value-name (slice u8)) (help (slice u8))] (-> Flag))
(defn switch [(long (slice u8)) (short i64) (help (slice u8))] (-> Flag))

(defstruct Command [(name (slice u8)) (group (slice u8)) (summary (slice u8))
                    (usage (slice u8)) (args (slice (slice u8))) (flags (slice Flag))])
(defstruct Program [(name (slice u8)) (summary (slice u8)) (version (slice u8))
                    (commands (slice Command)) (flags (slice Flag))])
(defn no-args [] (-> (slice (slice u8))))
(defn no-flags [] (-> (slice Flag)))
```

* `short` is an ASCII letter/digit (`#\p`) or `0` for none.
* A command `name` may be several words (`"site add"`); `""` is the root command
  run when no command word is given.
* `args` names positionals: `NAME` required, `[NAME]` optional, a final
  `NAME...`/`[NAME...]` takes the rest.
* `group` titles the section listing the command in program help (`""` means
  "Commands"); sections appear in order of first use.
* `usage` overrides the synthesized usage line; write it without the program
  name (`"pull RUN [DEST]"`).
* `Program.flags` are accepted with every command, before or after its words.
  `--help`/`-h` always exists; `--version` exists when `version` is non-empty.

Build the table where it is used (normally `main`). Array literals are frame
storage, so a helper that *returns* a `Program` built from literals would hand
back dangling slices. (Compile-time `const` tables do not work for this shape
today; see the compiler notes at the end.)

### Parsing

```coil
(defn argv-from-main [(a (dyn Allocator)) (argc i32) (argv (ptr (ptr i8)))] (-> (slice (slice u8))))
(defn parse [(a (dyn Allocator)) (prog Program) (argv (slice (slice u8)))] (-> Parsed))

(defsum Parsed (Run [(invocation Invocation)]) (Help [(target HelpTarget)]) (Version) (Failed [(error ArgError)]))
(defsum HelpTarget (ProgramHelp) (GroupHelp [(prefix (slice u8))]) (CommandHelp [(command i64)]))
(defsum ArgError
  (UnknownCommand [(input (slice u8)) (suggestion (slice u8))])
  (MissingSubcommand [(prefix (slice u8))])
  (UnknownFlag [(command i64) (given (slice u8)) (suggestion (slice u8))])
  (MissingValue [(command i64) (given (slice u8))])
  (UnexpectedValue [(command i64) (given (slice u8))])
  (MissingArgument [(command i64) (name (slice u8))])
  (UnexpectedArgument [(command i64) (value (slice u8))]))
```

`argv[0]` is skipped. Supported spellings: `--flag value`, `--flag=value`,
`-f value`, `-fvalue`, bundled switches `-dq`, `--` (everything after is
positional), and `-` as a positional. The longest matching multi-word command
wins; a bare group word (`site`) is `MissingSubcommand` (or `GroupHelp` with
`--help`). `--help` anywhere before `--` wins over other errors. Suggestions use
optimal-string-alignment distance (within a third of the typed length) and fall
back to a candidate the input is a prefix of. A command index of `-1` in an
error means it arose before a command was identified.

### Reading an invocation

```coil
(defn command-is? [(inv Invocation) (name (slice u8))] (-> bool))
(defn flag? [(inv Invocation) (long (slice u8))] (-> bool))
(defn flag-value [(inv Invocation) (long (slice u8))] (-> (Option (slice u8))))      ; last occurrence
(defn flag-value-or [(inv Invocation) (long (slice u8)) (fallback (slice u8))] (-> (slice u8)))
(defn flag-values [(a (dyn Allocator)) (inv Invocation) (long (slice u8))] (-> (slice (slice u8))))
(defn positional [(inv Invocation) (i i64)] (-> (Option (slice u8))))
(defn positional-count [(inv Invocation)] (-> i64))
(defn positionals [(inv Invocation)] (-> (slice (slice u8))))
```

`Invocation` also exposes `command` (index into `Program.commands`) and `name`.
Asking about a flag the command and program never declared aborts; asking
`flag-value` of a switch aborts.

### Help and errors

```coil
(defn render-help [(a (dyn Allocator)) (term Term) (prog Program) (target HelpTarget)] (-> (slice u8)))
(defn render-program-help [(a (dyn Allocator)) (term Term) (prog Program)] (-> (slice u8)))
(defn render-group-help [(a (dyn Allocator)) (term Term) (prog Program) (prefix (slice u8))] (-> (slice u8)))
(defn render-command-help [(a (dyn Allocator)) (term Term) (prog Program) (index i64)] (-> (slice u8)))
(defn render-error [(a (dyn Allocator)) (term Term) (prog Program) (error ArgError)] (-> (slice u8)))
(defn handle-parsed [(a (dyn Allocator)) (prog Program) (parsed Parsed)] (-> (Result Invocation i64)))
(defn parse-standard [(a (dyn Allocator)) (prog Program) (argv (slice (slice u8)))] (-> (Result Invocation i64)))
(defn edit-distance [(a (dyn Allocator)) (left (slice u8)) (right (slice u8))] (-> i64))
```

Program help:

```
Run AI-agent software factories anywhere.

Usage:
  factory <command> [flags]

Sites:
  site add   Add a remote site
  site rm    Remove a site

Runs:
  start      Start a run

Flags:
      --json      Print JSON
  -h, --help      Show help
      --version   Show the version

Run 'factory <command> --help' for more about a command.
```

Errors name the problem, suggest a fix, and point at help:

```
error: unknown flag '--detahc' for 'factory start'

  Did you mean '--detach'?

Run 'factory start --help' for usage.
```

Headings are bold, quoted names cyan and `error:` bold red when the terminal
has color. `handle-parsed` prints help/version to stdout (`Err 0`) and errors to
stderr (`Err 2`), styled for each stream.

## cli.style

### Capability

```coil
(defstruct Term [(color bool) (unicode bool)])
(defn term [(color bool) (unicode bool)] (-> Term))
(defn term-plain [] (-> Term))          ; no color, Unicode glyphs
(defn term-ascii [] (-> Term))          ; no color, ASCII glyphs
(defn term-detect [(fd i32)] (-> Term))
(defn term-stdout [] (-> Term))
(defn term-stderr [] (-> Term))
(defn tty? [(fd i32)] (-> bool))
(defn color-enabled? [(fd i32)] (-> bool))
(defn unicode-enabled? [] (-> bool))
(defn utf8-locale? [(locale (slice u8))] (-> bool))
```

Color: `FORCE_COLOR` (anything but `0`/`false`) forces it on and `0`/`false`
off; otherwise non-empty `NO_COLOR` or `TERM=dumb` turns it off; otherwise it is
on exactly when the descriptor is a terminal. Unicode glyphs: the first
non-empty of `LC_ALL`, `LC_CTYPE`, `LANG` names UTF-8 and `TERM` is not `dumb`.

### Painting

```coil
(defsum Color (Default) (Black) (Red) (Green) (Yellow) (Blue) (Magenta) (Cyan) (White) (Gray)
              (Rgb [(r u8) (g u8) (b u8)]))
(defn rgb [(r i64) (g i64) (b i64)] (-> Color))
(defstruct Style [(fg Color) (bold bool) (dim bool) (italic bool) (underline bool)])
(defn style [] (-> Style))
(defn fg [(color Color)] (-> Style))
(defn bold [(s Style)] (-> Style))      ; also dim, italic, underline
(defn paint [(a (dyn Allocator)) (term Term) (s Style) (text (slice u8))] (-> (slice u8)))
(defn paint-into! [(out (mut StrBuf)) (term Term) (s Style) (text (slice u8))] (-> i64))
```

`(paint a t (bold (fg (Red))) "x")` is `ESC[1;31mxESC[0m` with color and `x`
without. Semantic helpers, each `[(a) (term) (text)] -> (slice u8)` with a
matching `*-style` constructor: `ok` (green), `warn` (yellow), `err` (bold red),
`muted` (gray), `accent` (cyan), `strong` (bold).

### Glyphs

Each takes a `Term` and returns the Unicode glyph or its ASCII fallback:
`glyph-dot` ● `*`, `glyph-circle` ○ `o`, `glyph-target` ◉ `@`,
`glyph-diamond` ◆ `+`, `glyph-check` ✓ `v`, `glyph-cross` ✗ `x`,
`glyph-warning` ⚠ `!`, `glyph-arrow` → `->`, `glyph-hline` ─ `-`,
`glyph-vline` │ `|`, `glyph-ellipsis` … `...`.

### Width and tables

```coil
(defn display-width [(text (slice u8))] (-> i64))
(defn codepoint-width [(cp i64)] (-> i64))
(defn pad-right [(a (dyn Allocator)) (text (slice u8)) (width i64)] (-> (slice u8)))
(defn pad-left [(a (dyn Allocator)) (text (slice u8)) (width i64)] (-> (slice u8)))
(defn push-spaces! [(out (mut StrBuf)) (count i64)] (-> i64))

(defsum Align (AlignLeft) (AlignRight))
(defn table-new [(a (dyn Allocator)) (headers (slice (slice u8)))] (-> Table))
(defn table-new-headless [(a (dyn Allocator)) (columns i64)] (-> Table))
(defn table-align! [(table (mut Table)) (column i64) (align Align)] (-> i64))
(defn table-row! [(table (mut Table)) (cells (slice (slice u8)))] (-> i64))
(defn table-row-count [(table Table)] (-> i64))
(defn table-render [(a (dyn Allocator)) (term Term) (table Table)] (-> (slice u8)))
(defn table-free! [(table (mut Table))] (-> i64))
```

`display-width` counts terminal columns: East Asian wide characters and emoji
are 2, combining marks, zero-width format characters, variation selectors and
skin-tone modifiers 0, a scalar after a zero-width joiner 0 (so an emoji ZWJ
sequence measures as one emoji), control characters 0; CSI (colors) and OSC
(hyperlinks) escape sequences take no columns; malformed UTF-8 counts one
column per bad byte. Tables separate columns by two spaces, align by display
width (so painted and CJK cells line up), paint headers muted, and trim trailing
whitespace. Cells are borrowed.

```
NAME  STATUS   AGE
日本  ok        3m
abc   running  12s
```

### Humanized values

```coil
(defn relative-time [(a (dyn Allocator)) (delta-ms i64)] (-> (slice u8)))      ; "just now" "12s" "4m" "3h" "2d" "5mo" "1y" "in 4m"
(defn relative-time-ago [(a (dyn Allocator)) (delta-ms i64)] (-> (slice u8)))  ; "4m ago"
(defn human-bytes [(a (dyn Allocator)) (n i64)] (-> (slice u8)))               ; "512 B" "1.5 KiB" "3.4 GiB"
(defn human-count [(a (dyn Allocator)) (n i64)] (-> (slice u8)))               ; "999" "1.2k" "12k" "3.4M" "2B"
(defn plural [(n i64) (singular (slice u8)) (plural-form (slice u8))] (-> (slice u8)))
(defn count-noun [(a (dyn Allocator)) (n i64) (singular (slice u8)) (plural-form (slice u8))] (-> (slice u8)))  ; "3 runs"
```

Below ten units one decimal is shown (dropped when zero); a value that rounds up
to the next unit moves there (`999999` is `1M`, not `1000k`).

## cli.prompt

```coil
(defsum PromptError (PromptEof) (PromptIo [(code i64)]) (InvalidAnswer [(message (slice u8))]))
(defn prompter-new [(a (dyn Allocator))] (-> Prompter))                      ; stdin, prompts on stderr
(defn prompter-with [(a (dyn Allocator)) (in-fd i32) (out-fd i32) (term Term) (interactive bool)] (-> Prompter))
(defn prompter-free! [(p (mut Prompter))] (-> i64))
(defn ask [(p (mut Prompter)) (question (slice u8)) (default (slice u8))] (-> (Result (slice u8) PromptError)))
(defn ask-multiline [(p (mut Prompter)) (question (slice u8))] (-> (Result (slice u8) PromptError)))
(defn choose [(p (mut Prompter)) (question (slice u8)) (options (slice (slice u8))) (default i64)] (-> (Result i64 PromptError)))
(defn confirm [(p (mut Prompter)) (question (slice u8)) (default bool)] (-> (Result bool PromptError)))
(defn read-line! [(p (mut Prompter))] (-> (Result (Option (slice u8)) PromptError)))
(defn read-fd-all [(a (dyn Allocator)) (fd i32)] (-> (Result (slice u8) PromptError)))
(defn read-stdin-all [(a (dyn Allocator))] (-> (Result (slice u8) PromptError)))
```

```
? Project name (demo): 
? Where should it run?
  1) local
  2) metaphysics
  Choose 1-2 (local): x
  ✗ enter a number from 1 to 2 or an option's name
  Choose 1-2 (local): 2
? Start now? [Y/n]: 
```

On a terminal an invalid answer is explained and asked again, and end of input
(Ctrl-D) is `PromptEof`. When stdin is not a terminal nothing is printed and
each answer is the next line, so scripts can pipe answers in; there an invalid
answer is `InvalidAnswer` and running out of input is `PromptEof` (never an
implicit default). `choose` accepts a number or an option's text (ASCII case
ignored); `default` is an index or `-1` for none. `ask-multiline` reads until a
line holding only `.` or end of input and joins lines with `\n`. `\r\n` line
endings are accepted. Use one `Prompter` per input stream: it buffers input read
past the current line.

## Tests

`coil test` runs 41 tests: argument parsing in every spelling, errors and
suggestions, exact help and error text (plain and colored), color and Unicode
policy from the environment, display width with wide/combining/emoji/escapes,
table alignment, humanized values, and prompts over pipes (interactive and
piped modes).

## Compiler notes

Found while building this package (minimal repros are in the task report):
constant tables of commands cannot be written as `const` today — a `const`
struct array whose slice field refers to another `const` array fails with
"internal: make-slice data is not a pointer", and one with a nested array
literal fails with "value cannot be materialized". Build tables at runtime in
`main` instead, as above.
