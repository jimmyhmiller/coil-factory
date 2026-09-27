# agent-tools

The workspace tools an AI coding agent uses: file tools confined to one
directory, and a shell tool that can run every command inside an OS sandbox.
Each tool implements `agent.tool/Tool` from `libraries/agent`.

```toml
[dependencies]
agent = { path = "../coil-factory/libraries/agent" }
agent_tools = { path = "../coil-factory/libraries/agent-tools" }
```

| Namespace | Contents |
| --- | --- |
| `agent_tools.workspace` | `Workspace`, path resolution and confinement |
| `agent_tools.files` | `read_file`, `write_file`, `edit_file`, `list_dir`, `search` |
| `agent_tools.shell` | the `shell` tool, and `run-command` for non-agent callers |
| `agent_tools.sandbox` | `SandboxPolicy`, the `sandbox-exec` / `bwrap` wrappers |
| `agent_tools.bundle` | `standard-tools`: every tool for a workspace in a `ToolRegistry` |

`agent_tools.sys` (libc bindings) and `agent_tools.text` (UTF-8 and string
helpers) are internal support modules.

All strings a function returns are allocated in the allocator passed to it.
Tools borrow their `Workspace`; keep it alive as long as the tools.

## Quick start

```coil
(import "coil.alloc" :use *)
(import "agent.tool" :use *)
(import "agent_tools.bundle" :use *)
(import "agent_tools.sandbox" :use *)
(import "agent_tools.workspace" :use *)

(defn tools-for-run [(a (dyn Allocator)) (root (slice u8))] (-> (Result StandardTools (slice u8)))
  (match (workspace-open a root)                       ; absolute path; realpath'd
    (Err [message] (Err message))
    (Ok [workspace]
        (match (workspace-sandbox-options a workspace false)  ; no network, + git dirs
          (Err [message] (Err message))
          (Ok [options]
              (let [policy (sandbox-detect a options)]
                ;; A site configured `isolation = sandbox` must not fall back silently.
                (match policy
                  (NoSandbox [] (Err "isolation = sandbox, but neither sandbox-exec nor bwrap is available"))
                  (_ (standard-tools a workspace policy ["files" "shell"])))))))))
```

`(.registry tools)` is an ordinary `ToolRegistry`: hand it to the agent loop,
look tools up with `registry-find`, and call `standard-tools-free!` when the
run ends.

A factory gate runs a command exactly as the agent's `shell` tool would:

```coil
(import "agent_tools.shell" :use *)

(match (run-command a workspace policy "coil test" 600000
                    (box! a NeverCancelled (NeverCancelled :unused 0)))
  (Err [message] (report-infrastructure-failure message))   ; could not start
  (Ok [finished]
      (let [result finished]
        (if (and (= (.exit-code result) 0) (not (.timed-out result)))
            (gate-passed)
            (gate-failed (.output result))))))
```

## Workspace

```coil
(defstruct Workspace [(root (slice u8))])
(defstruct ResolvedPath [(absolute (slice u8)) (relative (slice u8))])

(workspace-open a path)        ; (Result Workspace (slice u8))
(workspace-root workspace)     ; canonical absolute root
(workspace-contains? workspace canonical-absolute-path)
(resolve a workspace model-path) ; (Result ResolvedPath (slice u8))
```

`workspace-open` requires an absolute path to an existing directory and
canonicalizes it with `realpath` (on macOS `/tmp/x` becomes `/private/tmp/x`).

`resolve` turns a path the model supplied into one the tools may open. It
refuses, with a message the model can act on:

- the empty path, and paths containing a NUL byte;
- absolute paths (an absolute path inside the workspace gets the suggestion
  of its relative spelling);
- `..` that climbs above the root (`a/../b` is fine and becomes `b`);
- symbolic links whose target is outside the workspace: the longest existing
  prefix of the path is `realpath`'d and must stay under the root;
- dangling symbolic links, which `write_file` would otherwise follow out of
  the workspace.

`relative` is the normalized spelling (`./src//x` → `src/x`, the root is `.`).
The check happens before the operation, so a process racing to swap a
directory for a symlink between check and use is not stopped by it; the
sandbox is the boundary for adversarial code, and it confines `shell`.

## File tools

Each tool is a struct holding the workspace; build one with its constructor
and register a pointer to it (the registry borrows):

```coil
(let [read (box! a ReadFileTool (read-file-tool workspace))]
  (registry-add! (mut registry) read))
```

| Tool | Arguments | Summary |
| --- | --- | --- |
| `read_file` | `path`, `offset?` (1-based, default 1), `limit?` (lines, default 400) | `read src/x.coil` |
| `write_file` | `path`, `content` | `write src/x.coil` |
| `edit_file` | `path`, `old_text`, `new_text`, `replace_all?` (default false) | `edit src/x.coil` |
| `list_dir` | `path?` (default `.`) | `ls src` |
| `search` | `pattern`, `path?` (default `.`), `glob?` | `search "pattern"` |

Every schema uses only the JSON Schema subset `json_schema.validate` accepts
(`type`, `properties`, `required`, `additionalProperties: false`,
`description`); defaults and limits are stated in the descriptions. A bad
argument (wrong type, `offset` below 1, missing `path`) is a `ToolFailed`
naming the argument.

**read_file** streams the file, so large files are cheap to window. Lines are
numbered `   12│ text`. Output stops at 50 KB (on a line boundary) and a line
longer than 2000 bytes is cut with a note giving its length. When there is
more, the output ends with
`(showing lines 1-400; the file continues. Read more with offset=401)`. A file
with a NUL byte in its first 8 KB is refused as binary; invalid UTF-8 is
shown with U+FFFD. Reading past the end says how many lines the file has.

**write_file** creates missing parent directories and reports
`created src/x.coil (120 bytes, 4 lines)` or `overwrote …`.

**edit_file** requires `old_text` to match exactly. Not found, or found more
than once without `replace_all`, is a `ToolFailed` that says nothing changed
and what to do (the count is included). On success it returns
`edited src/x.coil: replaced 1 occurrence` and the edited region with three
lines of context, numbered like `read_file`. Files over 16 MB and binary files
are refused.

**list_dir** prints one entry per line, sorted bytewise; directories end in
`/`, symbolic links show `name -> target`; `.git` is skipped; at most 500
entries, then `… N more entries not shown`.

**search** runs `rg` when it is on `PATH`, otherwise `grep -rnIHE`, with the
workspace root as the working directory, so results are `path:line:text`
relative to the root. Ripgrep honors `.gitignore` and includes hidden files;
both skip binary files and `.git`. At most 250 lines (32 KB) are returned,
then `(showing 250 of N matching lines; …)`. No match is a `ToolOk`
(`no matches for "x" in "."`); an invalid pattern is a `ToolFailed` with the
search program's message. Searches time out after 60 s and honor
cancellation.

```coil
(search-backend-detect a)          ; (Result SearchBackend (slice u8))
(defsum SearchBackend (Ripgrep [(program (slice u8))]) (Grep [(program (slice u8))]))
(search-tool workspace backend)
```

## Shell

```coil
(defstruct CommandResult
  [(exit-code i64)       ; exit status, or 128 + signal
   (signal i64)          ; the terminating signal, else 0
   (timed-out bool)
   (cancelled bool)
   (output (slice u8))   ; stdout+stderr merged, first 8 KB + last 8 KB, UTF-8
   (output-bytes i64)    ; everything the command wrote
   (output-lines i64)
   (omitted-bytes i64)]) ; dropped from the middle

(run-command a workspace policy command timeout-ms context)  ; (Result CommandResult (slice u8))
(command-report a result timeout-ms)                         ; the shell tool's text
(shell-tool workspace policy)                                ; the `shell` Tool
```

`run-command` runs `/bin/sh -c command` (wrapped by the sandbox policy) with
the workspace root as working directory, stdin from `/dev/null`, stderr
merged into stdout, in a new process group. It polls about every 100 ms for
output, exit, `tool-cancelled?`, and the deadline.

- On timeout or cancellation the whole process group gets SIGTERM, then
  SIGKILL after 2 s.
- When the command exits, whatever it left in its process group (background
  jobs, `cmd &`) is stopped the same way, so nothing outlives the call. The
  shell is not reaped until then (`waitid` with `WNOWAIT`), so its process
  group id cannot be reused by an unrelated process while it is signalled.
- Output is drained until every writer has closed the pipe, bounded by the
  same grace periods.
- `Err` only when the program could not be started (a missing working
  directory or sandbox program) or its status could not be collected. A
  failing command is `Ok` with its exit code.

The `shell` tool takes `command` and `timeout_seconds?` (default 300, 1 to
3600). Its text is the output followed by one status line: `exit 0`,
`exit 137 (killed by signal 9)`,
`timed out after 5s; the command and its child processes were stopped`, or
`cancelled; …`. Empty output shows `(no output)`. A nonzero exit is a
`ToolOk`, because the model needs to read the failure; only a failure to
start is `ToolFailed`. The summary is `$ ` and the first line of the command,
cut to about 80 bytes with `…`.

## Sandbox

```coil
(defstruct SandboxOptions [(allow-network bool) (extra-writable (slice (slice u8)))])
(defsum SandboxPolicy (NoSandbox) (MacSandbox [(options SandboxOptions)]) (Bubblewrap [(options SandboxOptions)]))

(sandbox-options allow-network)
(sandbox-options-with-paths allow-network extra-writable)
(sandbox-detect a options)                     ; best policy available here
(sandbox-argv a policy root command)           ; full argv, argv[0] is the program
(workspace-git-dirs a workspace)               ; git dirs outside the workspace
(workspace-sandbox-options a workspace allow-network)
```

**macOS** runs `/usr/bin/sandbox-exec -p PROFILE /bin/sh -c command` with

```
(version 1)(allow default)(deny file-write*)
(allow file-write* (subpath "<root>") (subpath "/private/tmp")
  (subpath "/private/var/tmp") (subpath "/private/var/folders")
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty")
  (literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/dtracehelper")
  (subpath "/dev/fd") <extra-writable subpaths>)
```

plus `(deny network-outbound (remote ip))(deny network-inbound (local ip))`
unless the network is allowed. Reads are unrestricted. `$TMPDIR` lives under
`/private/var/folders`, so compilers and git keep working, and `coil build`
writes its `.coil` directory inside the workspace. Paths are written as SBPL
string literals with `"` and `\` escaped. Every path must be canonical: the
sandbox compares resolved paths.

**Linux** runs

```
bwrap --die-with-parent --new-session --unshare-pid --ro-bind / / --dev /dev
      --proc /proc --tmpfs /tmp --bind ROOT ROOT [--bind P P]...
      --chdir ROOT [--unshare-net] -- /bin/sh -c COMMAND
```

The system is read-only, the workspace writable, `/tmp` private, and the PID
namespace dies with `bwrap`, so killing the process group kills everything.

**Git worktrees.** A worktree's `.git` is a file pointing into the main
repository, and a commit writes objects there. `workspace-git-dirs` reads
the `.git` file and its `commondir` and returns the main repository's `.git`
(canonical); `workspace-sandbox-options` adds it to `extra-writable`. A plain
checkout needs nothing extra.

`sandbox-detect` returns `MacSandbox` when `/usr/bin/sandbox-exec` exists on
macOS, `Bubblewrap` when `bwrap` is on `PATH` on Linux, and otherwise
`NoSandbox`. A caller that requires isolation must reject `NoSandbox`.

## Bundle

```coil
(standard-tools a workspace policy groups)  ; (Result StandardTools (slice u8))
(standard-tools-free! (mut tools))
```

`groups` is a subset of `"files"` (read_file, write_file, edit_file,
list_dir, search) and `"shell"`. Names match exactly; an unknown or repeated
name is an `Err` listing the valid names. The tools are allocated in `a`, and
`StandardTools` owns them and the registry.

## Tests

`coil test` runs every suite against real temporary workspaces
(`/private/tmp/agent-tools-test-<pid>-<label>`, and `$HOME/.cache/…` for the
sandbox tests, since `/private/tmp` is writable by policy). They cover path
confinement, read windows and limits, writes, edits, listings, both search
backends, exit codes, output capping, timeouts and cancellation killing child
processes, the real macOS sandbox (writes outside blocked, inside allowed,
`mktemp`, git commits in a worktree), exact `bwrap` argv, and schema
validation through `json_schema.validate`.
