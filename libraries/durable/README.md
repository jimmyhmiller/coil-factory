# durable

Crash-safe filesystem primitives for programs that keep their state in plain
files: a multi-writer JSON Lines journal with byte-offset cursors, atomic file
replacement, advisory locks and pid files, and sortable identifiers.

```toml
[dependencies]
durable = { path = "../coil-factory/libraries/durable" }
```

Runs on macOS and Linux (x86_64 and aarch64). Platform constants are chosen at
compile time for the target; any other target OS is a compile error.

## Errors

Every fallible operation returns `(Result T DurableError)`:

```lisp
(defsum DurableError
  (Os [(op (slice u8)) (code i64)])          ; a syscall failed: op name + errno
  (Invalid [(reason (slice u8))])            ; argument refused (newline in a record, NUL in a path, ...)
  (BadCursor [(offset i64) (reason (slice u8))])
  (Corrupt [(reason (slice u8))])            ; on-disk content has the wrong shape
  (OutOfMemory))
```

`(error-describe a err)` renders a one-line message (allocator-owned);
`(error-os-code err)` returns the errno of an `Os` error. Nothing in the library
returns a default in place of an error.

Paths are `(slice u8)`. Every function that needs a C path takes an allocator
first; an empty path or one containing a NUL byte is `Invalid`.

## `durable.jsonl` — the journal

```lisp
(journal-append! a path line sync)            ; -> (Result i64 DurableError): offset of the record
(journal-append-with-mode! a path line sync mode)
(journal-read a path from max-records max-bytes) ; -> (Result JournalBatch DurableError)
(journal-size a path)                         ; -> (Result i64 DurableError), 0 when missing
(journal-batch-free! (mut batch))
(batch-records batch)   ; (slice JournalRecord)
(batch-count batch)     ; i64
(batch-next-offset batch)

(defstruct JournalRecord [(offset i64) (next-offset i64) (line (slice u8))])

(follower-new a path cursor max-records max-bytes) ; -> (Result Follower DurableError)
(follower-poll! (mut follower))                    ; -> (Result JournalBatch DurableError)
(follower-cursor follower)
(follower-free! (mut follower))
```

**Appending.** `line` is one record without its newline; it must be nonempty and
contain no `\n`. The file is opened `O_RDWR|O_APPEND|O_CREAT` (mode 0600 unless
you pass one) on a descriptor private to the call, and the record is written
under `flock(LOCK_EX)`, looping over partial writes. Any number of threads and
processes may append concurrently: records never interleave and the returned
offset is exactly where the record starts. With `sync`, the record is fsync'd
before the lock is released, and the directory is fsync'd after the first record.

A writer that dies mid-record leaves an incomplete final line. Readers never
return it, and the next `journal-append!` (holding the lock, so no live writer
can be mid-record) truncates it before appending. All writers must go through
`journal-append!` for these guarantees.

**Reading.** Cursors are byte offsets. `from` must be 0 or an offset the library
returned (`next-offset` of a record, or an append's return value); negative
offsets, offsets past the end, and offsets not directly after a `\n` are
`BadCursor`. A missing journal reads as an empty batch at offset 0. The read
uses `pread` from `from` and never touches earlier bytes, so paging through a
large journal is linear. A batch holds at most `max-records` records and at most
`max-bytes` bytes of records, except that the first record is always returned
whole, so a reader always makes progress. Record lines borrow the batch's
buffer: copy what you keep, then free the batch.

```lisp
(import "durable.jsonl" :use *)
(import "durable.error" :use *)

(let [a (malloc-allocator)
      path "/var/factory/runs/0mfz3k1q2-9c41be07/events.jsonl"]
  (match (journal-append! a path "{\"t\":1700000000000,\"kind\":\"run.started\"}" true)
    (Ok [offset] offset)
    (Err [e] (do (println "{s}" (error-describe a e)) -1)))

  ;; page through everything, 500 records / 1 MiB at a time
  (let [(mut cursor) 0]
    (loop
      (match (journal-read a path (load cursor) 500 1048576)
        (Err [e] (break))
        (Ok [b]
            (let [(mut batch) b]
              (for r (iter (batch-records (load batch)))
                (let [record r]
                  (handle-line (.line record))))
              (set! cursor (batch-next-offset (load batch)))
              (let [n (batch-count (load batch))]
                (journal-batch-free! (mut batch))
                (if (= n 0) (break) 0))))))))
```

**Tailing.** A `Follower` remembers its cursor; each `follower-poll!` returns the
complete records appended since the last poll (possibly none) and advances. Save
`follower-cursor` to resume later with `follower-new`.

```lisp
(let [(mut f) (try! (follower-new a path saved-cursor 256 1048576))]
  (loop
    (let [(mut batch) (try! (follower-poll! (mut f)))]
      (for r (iter (batch-records (load batch))) (let [record r] (publish (.line record))))
      (journal-batch-free! (mut batch))
      (sleep-a-little)))
  (follower-free! (mut f)))
```

## `durable.atomic` — files and directories

```lisp
(write-file-atomic! a path data mode)   ; -> (Result i64 DurableError): bytes written
(read-file-if-exists a path)            ; -> (Result (Option (slice u8)) DurableError)
(ensure-dir! a path mode)               ; mkdir -p
(remove-tree! a path)                   ; rm -rf, guarded; -> entries removed
(remove-tree-refusal path)              ; -> (Option (slice u8)): why a path is refused
(path-kind a path)                      ; -> (Result PathKind DurableError): Missing | Directory | NotDirectory
(file-exists? a path)                   ; -> (Result bool DurableError)
(dir-exists? a path)
(list-dir a path)                       ; -> (Result (ArrayList (slice u8)) DurableError), sorted bytewise
(list-dir-free! a (mut names))
```

`write-file-atomic!` writes `<dir>/.<name>.tmp-<pid>-<random>` with `O_EXCL`,
fsyncs it, renames it over `path`, and fsyncs the directory: readers (and a
machine that loses power) see the old or the new content, never a mix. On
failure the temporary is removed and `path` is untouched.

`read-file-if-exists` returns `None` only for a missing file; a directory or a
permission problem is an error. The bytes are owned by `a`
(`(free-bytes a bytes 1)`).

`ensure-dir!` succeeds when the directory already exists; an existing
non-directory is `Os "mkdir"` with EEXIST (the path itself) or ENOTDIR (a parent).

`remove-tree!` walks with `openat`/`unlinkat` and `O_NOFOLLOW`: symbolic links
are removed, never followed, including when `path` itself is a link. It returns
0 when nothing was there. It refuses (`Invalid`): an empty path; a path shorter
than 8 bytes; the root in any spelling; any `.` or `..` component (`./x`,
`a/../b`); and an absolute path with fewer than two components (`/tmp`,
`/home`). These guards stop the usual accidents — an empty variable, a
truncated path, a stray parent reference — not every destructive call.

`path-kind`, `file-exists?` and `dir-exists?` follow symbolic links and report
unexpected failures (for example EACCES on a parent) as errors instead of
guessing.

```lisp
(try! (ensure-dir! a "/var/factory/runs/abc" 448))
(try! (write-file-atomic! a "/var/factory/runs/abc/run.json" spec-json 420))
(match (try! (read-file-if-exists a "/var/factory/runs/abc/pid"))
  (None [] "not running")
  (Some [bytes] ...))
```

## `durable.lock` — locks and pid files

```lisp
(defstruct FileLock [(fd i32) (held bool)])
(lock-open! a path)          ; -> (Result FileLock DurableError), creates mode 0600
(lock-try! (mut lock))       ; -> (Result bool DurableError): false when held elsewhere
(lock-wait! (mut lock))      ; blocks
(unlock! (mut lock))
(lock-held? lock)
(lock-close! (mut lock))     ; releases; closing twice is Invalid

(pid-write! a path pid)      ; atomic "<pid>\n", mode 0644
(pid-read a path)            ; -> (Result (Option i64) DurableError); garbage is Corrupt
(pid-alive? pid)             ; -> (Result bool DurableError)
```

Locks are `flock` locks on the lock value's own descriptor: two `FileLock`s on
one file exclude each other even in one process, and the kernel drops the lock
when the holder exits or crashes. They are advisory.

`pid-alive?` sends signal 0: EPERM (someone else's process) is alive, ESRCH is
dead, and an unreaped zombie still counts as alive. Pids ≤ 0 are `Invalid`
because `kill(0, …)` and `kill(-1, …)` address process groups.

```lisp
(let [(mut lock) (try! (lock-open! a "/var/factory/daemon.lock"))]
  (if (try! (lock-try! (mut lock)))
      (do (try! (pid-write! a "/var/factory/daemon.pid" (cast i64 (os/getpid))))
          (serve)
          (try! (lock-close! (mut lock))))
      (println "another daemon is running")))
```

## `durable.ids` — identifiers

```lisp
(random-fill! out)           ; fill a byte slice from getentropy(3)
(random-hex a byte-count)    ; -> (Result (slice u8) DurableError): 2*n lowercase hex chars
(now-ms)                     ; -> i64, wall clock ms since the epoch
(time-id a)                  ; -> (Result (slice u8) DurableError)
(time-id-at a ms)
```

A time id is nine zero-padded base-36 digits of the millisecond timestamp, `-`,
and eight random hex characters: `0mfz3k1q2-9c41be07`. Ids sort by creation
time as plain strings (to the millisecond; ids from the same millisecond order
randomly). Nine digits last until the year 5138. `now-ms` aborts with a message
if `clock_gettime(CLOCK_REALTIME)` fails, which a supported platform never does.

## Internal modules

`durable.sys` declares every libc symbol the package adds (once, as Coil
requires) and the per-platform constants. `durable.fdio` holds the descriptor
loops (EINTR retry, partial writes, `pread`, `fsync`, directory listing). They
are importable but not part of the stable surface.

## Tests

`coil test` runs 24 tests, including a concurrency proof: four forked processes
with four threads each append 200 records of varying size (up to 5 KB) to one
journal; every append's offset is re-read and must hold that exact record, and
the final journal must contain each record exactly once, whole, in per-thread
order. Removing the `flock` makes this test fail. Tests use unique directories
under `/tmp/durable-test-<pid>-…` and delete them.
