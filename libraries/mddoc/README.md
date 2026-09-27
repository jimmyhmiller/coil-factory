# mddoc

Parse Markdown documents that carry a front-matter header and are organized by
headings — the shape of factory definitions, agent prompts, and runbooks — into
typed front-matter entries and sections with line numbers.

```toml
[dependencies]
mddoc = { path = "../coil-factory/libraries/mddoc" }
```

```lisp
(import "mddoc.doc" :use *)
```

## Ownership

Every slice a `Document` returns borrows the input text, so **the text must
outlive the Document**. The exception is a quoted front-matter value that needed
unescaping; it is copied into the Document's allocator. `document-free!` releases
the Document's lists and those copies.

## Parsing

```lisp
(parse-document a text)       ; -> (Result Document DocError)
(document-free! (mut doc))

(defstruct DocError [(line i64) (message (slice u8))])   ; 1-based line, static message
```

### Front matter

Recognized only when the first line (after an optional UTF-8 BOM) is `---`. It
ends at the next line that is `---` (trailing blanks allowed); without one the
parse fails at line 1. Each line inside is one of:

- blank;
- a comment: `#` as the first non-blank character;
- `key: value`, with the key matching `[A-Za-z_][A-Za-z0-9_.-]*`, a colon, then a
  blank or the end of the line;
- `- item` (indentation allowed) continuing the block list opened by a preceding
  `key:` line with no value. Blank lines and comments may sit between items.

Values are

- plain: the rest of the line, trimmed, verbatim. `#` inside a plain value is
  **not** a comment (`title: Issue #42` is `Issue #42`);
- double-quoted: `\" \\ \/ \n \t \r \b \f \uXXXX` escapes (not surrogates);
- single-quoted: `''` is one quote;
- inline lists: `[a, "b c", 'd,e']` (`[]` is empty; empty items are errors).

Anything outside this subset is an error with its line number rather than a
silent misreading: duplicate keys, indented lines that are not list items,
`- item` with no open `key:`, `|`/`>` block scalars, `{...}` mappings, `&` `*` `!`
anchors/aliases/tags, nested lists, text after a closing quote, unknown escapes,
unterminated quotes or lists.

A key's value comes in three shapes, all available through `FrontEntry`:

| source | `is-list` | `scalar` | `items` |
| --- | --- | --- | --- |
| `key: value` | false | `value` | `[value]` |
| `key: [a, b]` or `key:` + `- a` `- b` | true | `""` | `[a, b]` |
| `key:` alone | false | `""` | `[]` |

```lisp
(front-get doc key)       ; -> (Result (Option (slice u8)) DocError)
                          ;    None: absent; Err (at the key's line): the value is a list
(front-list doc key)      ; -> (Option (slice (slice u8)))  scalar = one-element list
(front-csv a doc key)     ; -> (Option (ArrayList (slice u8)))  scalar split on commas,
                          ;    trimmed, empties dropped; free the list with al-free!
(front-entry doc key)     ; -> (Option FrontEntry)  with the key's line number
(front-entries doc)       ; -> (slice FrontEntry) in file order
(front-keys a doc)        ; -> (ArrayList (slice u8)) in file order
(doc-has-front-matter? doc)
```

### Headings and sections

The body (the text after the front matter) is split at ATX headings: up to three
spaces of indentation, one to six `#`, then a blank or the end of the line
(`#tag` is not a heading, a lone `##` is an empty one). The title is trimmed and
a closing run of `#`s is removed when a blank precedes it. Lines inside fenced
code blocks — opened by three or more backticks or tildes (indented at most three
spaces), closed by a fence of the same character at least as long, or running to
the end when unclosed — are never headings. Setext (underlined) headings are not
recognized.

```lisp
(defstruct Heading [(level i64) (title (slice u8)) (line i64)])
(defstruct Section
  [(level i64) (title (slice u8)) (line i64)
   (body (slice u8))      ; to the next heading of the same or higher level (includes subsections)
   (own-body (slice u8))  ; to the next heading of any level
   (body-line i64)        ; line number of the first body line
   (parent i64)])         ; index of the enclosing section, or -1

(doc-sections doc)        ; -> (slice Section), one per heading, document order
(doc-headings doc)        ; -> (slice Heading)
(find-section doc title)  ; -> (Option i64), first exact title match
(doc-preamble doc)        ; text before the first heading
(doc-body doc)            ; everything after the front matter
(doc-body-line doc)       ; its first line number
```

CRLF input is accepted. Titles, keys and values never contain the CR; `body`
slices keep the input's own line endings.

## Settings blocks

A section may begin with `key: value` lines that configure it:

```markdown
## Station: build
model: gpt-5
gate: make test

Implement the feature.
```

```lisp
(section-attributes a body first-line)  ; -> (Result SectionAttributes DocError)
(section-settings a section)            ; the same over a Section's body and body-line
(attr-get sa key)                       ; -> (Option (slice u8))
(section-attributes-free! (mut sa))

(defstruct Attribute [(key (slice u8)) (value (slice u8)) (line i64)])
(defstruct SectionAttributes [(attrs (ArrayList Attribute)) (rest (slice u8)) (rest-line i64)])
```

Blank lines before the block are skipped. The block is the run of consecutive
lines whose key matches `[A-Za-z][A-Za-z0-9_-]*` followed by `:` and a blank or
the end of the line (so `http://x` does not match); it stops at the first blank
or other line. Values are trimmed and verbatim — quotes stay. `rest` is what
follows, with leading blank lines removed; `rest-line` is its line number. A
key repeated within the block is an error at the repeated line.

## Example

```lisp
(defn load-factory [(a (dyn Allocator)) (text (slice u8))] (-> i64)
  (match (parse-document a text)
    (Err [err]
         (let [e err]
           (println "factory.md:{}: {s}" (.line e) (.message e))
           1))
    (Ok [parsed]
        (let [(mut doc) parsed]
          (match (front-get (load doc) "name")
            (Ok [name]
                (match name
                  (Some [n] (println "factory {s}" n))
                  (None [] (println "unnamed"))))
            (Err [e] (println "name must be a single value")))
          (for s (iter (doc-sections (load doc)))
            (let [section s]
              (when (and (= (.level section) 2) (starts-with (.title section) "Station: "))
                (match (section-settings a section)
                  (Ok [found]
                      (let [(mut settings) found]
                        (match (attr-get (load settings) "model")
                          (Some [m] (println "{s} uses {s}" (.title section) m))
                          (None [] (println "{s} uses the default model" (.title section))))
                        (section-attributes-free! (mut settings))))
                  (Err [err]
                       (let [e err]
                         (println "line {}: {s}" (.line e) (.message e))
                         0)))
                0)))
          (document-free! (mut doc))
          0))))
```

## Tests

`coil test` runs 9 suites of cases: scalars, quotes and escapes, inline and
block lists, CSV splitting, absent and empty front matter, a `---` rule that is
not front matter, CRLF with a BOM, code fences hiding `## not a heading`,
heading edge cases, nested levels with bodies and parents, settings blocks, and
error line numbers for every rejected construct.
