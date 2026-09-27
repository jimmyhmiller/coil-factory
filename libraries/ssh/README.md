# ssh

Reach other machines over the system `ssh`: parse destinations, run remote
commands with captured output and deadlines, and keep supervised local port
forwards. Written in Coil; the only external dependency is the `ssh` client on
`PATH`.

```toml
[dependencies]
ssh = { path = "../coil-factory/libraries/ssh" }
```

| Namespace | What |
| --- | --- |
| `ssh.target` | `user@host:port` / `ssh://…` / IPv6 parsing and rendering |
| `ssh.exec` | `ssh-run`, `ssh-reachable?`, `shell-quote`, `shell-join` |
| `ssh.tunnel` | `ssh -N -L` forwards: open, prove, ensure (reconnect with backoff), close |

Every invocation is non-interactive: `BatchMode=yes` (never prompt),
`ConnectTimeout`, `StrictHostKeyChecking=accept-new` by default (trust first
contact, refuse a changed key), and `LogLevel=ERROR` so stderr carries only
real problems. The operator's `~/.ssh/config` still applies: a host may be an
alias.

## ssh.target

```coil
(defstruct Target [(user (slice u8)) (host (slice u8)) (port i64)])   ; port 0 = ssh's default
(defn target [(user (slice u8)) (host (slice u8)) (port i64)] (-> Target))
(defn parse-target [(text (slice u8))] (-> (Result Target (slice u8))))
(defn target-destination [(a (dyn Allocator)) (t Target)] (-> (slice u8)))   ; "user@host"
(defn target-display [(a (dyn Allocator)) (t Target)] (-> (slice u8)))       ; "user@[::1]:2222"
(defn target-ssh-args [(a (dyn Allocator)) (t Target)] (-> (slice (slice u8))))  ; ["-p" "2222" "user@host"]
```

Accepted: `host`, `user@host`, `host:port`, `user@host:port`,
`ssh://[user@]host[:port][/]`, `[v6]:port`, `user@[v6]`, and a bare IPv6
address. Errors are short reasons ("the port must be between 1 and 65535").
A user or host beginning with `-` is rejected, because ssh would read it as an
option (`-oProxyCommand=…`).

```coil
(match (parse-target "deploy@build.example.com:2222")
  (Ok [t] ...)
  (Err [why] (println "invalid ssh target: {}" why)))
```

## ssh.exec

```coil
(defstruct SshOptions [(ssh-binary (slice u8)) (connect-timeout-seconds i64) (identity-file (slice u8))
                       (strict-host-key-checking (slice u8)) (max-output-bytes i64) (extra (slice (slice u8)))])
(defn ssh-options [] (-> SshOptions))   ; "ssh", 10 s, no identity, "accept-new", 16 MiB, no extras

(defstruct SshResult [(stdout (slice u8)) (stderr (slice u8)) (exit-code i64) (signal i64)
                      (timed-out bool) (stdout-truncated bool) (stderr-truncated bool)])
(defsum SshError (SshInvalidRequest [(message (slice u8))]) (SshSpawnFailed [(code i64)])
                 (SshIoFailed [(code i64)]) (SshOutOfMemory))

(defn ssh-run [(a (dyn Allocator)) (target Target) (opts SshOptions) (command (slice u8))
               (stdin (Option (slice u8))) (timeout-ms i64)] (-> (Result SshResult SshError)))
(defn ssh-reachable? [(a (dyn Allocator)) (target Target) (opts SshOptions) (timeout-ms i64)] (-> (Result bool SshError)))
(defn ssh-result-ok? [(result SshResult)] (-> bool))          ; exit 0 within the deadline
(defn ssh-connection-failed? [(result SshResult)] (-> bool))  ; exit 255: ssh itself failed
(defn ssh-result-free! [(a (dyn Allocator)) (result (mut SshResult))] (-> i64))
(defn ssh-error-message [(a (dyn Allocator)) (error SshError)] (-> (slice u8)))
(defn shell-quote [(a (dyn Allocator)) (word (slice u8))] (-> (slice u8)))
(defn shell-join [(a (dyn Allocator)) (words (slice (slice u8)))] (-> (slice u8)))
(defn ssh-exec-args [(a (dyn Allocator)) (target Target) (opts SshOptions) (command (slice u8))] (-> (slice (slice u8))))
(defn ssh-common-args [(a (dyn Allocator)) (args (mut (ArrayList (slice u8)))) (opts SshOptions)] (-> i64))
```

`ssh-run` runs `ssh … -T -e none DEST COMMAND` in its own process group.
`stdin`, when given, is streamed to the remote command (nonblocking, while
stdout and stderr are drained concurrently, so any sizes work) and then closed;
without it the remote reads end of file. A remote that stops reading early is
not an error — its exit status tells the story. `timeout-ms` covers the whole
exchange; on expiry the process group gets SIGTERM, then SIGKILL after 2 s, and
the result has `timed-out` with the output so far. Each stream is capped at
`max-output-bytes` (excess dropped, `*-truncated` set). `Err` means ssh could not
be run or supervised; connection failures are results with exit 255 and ssh's
explanation in `stderr`.

The remote side runs the command through the login shell, so quote untrusted
words:

```coil
(let [cmd (shell-join a ["git" "-C" repo "log" "--format=%H %s" "-n" "5"])]
  (match (ssh-run a host (ssh-options) cmd (None [(slice u8)]) 30000)
    (Ok [r] (let [result r]
              (if (ssh-result-ok? result)
                  (io/print-str (io/stdout) (.stdout result))
                  (io/print-str (io/stderr) (.stderr result)))))
    (Err [e] (io/print-str (io/stderr) (ssh-error-message a e)))))
```

`shell-quote` leaves `[A-Za-z0-9@%+=:,./_-]+` alone and single-quotes anything
else (`it's` → `'it'\''s'`, empty → `''`).

Writes to ssh's stdin block SIGPIPE on the calling thread and consume any
SIGPIPE they raise, so a vanished reader is an `EPIPE` result rather than a
killed process; no process-wide signal disposition is changed.

## ssh.tunnel

```coil
(defstruct TunnelSpec [(target Target) (remote-host (slice u8)) (remote-port i64) (options SshOptions)
                       (ready-timeout-ms i64) (keepalive-seconds i64)
                       (backoff-base-ms i64) (backoff-ceiling-ms i64)])
(defn tunnel-spec [(target Target) (remote-port i64)] (-> TunnelSpec))
(defsum TunnelStatus (TunnelReady [(port i64)]) (TunnelFailed [(message (slice u8))]))

(defn tunnel-new [(a (dyn Allocator)) (spec TunnelSpec)] (-> Tunnel))
(defn tunnel-ensure! [(tunnel (mut Tunnel))] (-> TunnelStatus))
(defn tunnel-open! [(tunnel (mut Tunnel))] (-> TunnelStatus))
(defn tunnel-close! [(tunnel (mut Tunnel))] (-> i64))
(defn tunnel-free! [(tunnel (mut Tunnel))] (-> i64))
(defn tunnel-alive? [(tunnel (mut Tunnel))] (-> bool))
(defn tunnel-local-port [(tunnel Tunnel)] (-> i64))
(defn tunnel-last-error [(tunnel Tunnel)] (-> (slice u8)))
(defn tunnel-base-url [(a (dyn Allocator)) (port i64)] (-> (slice u8)))   ; "http://127.0.0.1:PORT"
(defn tunnel-backoff-ms [(failures i64) (base i64) (ceiling i64)] (-> i64))
(defn tunnel-args [(a (dyn Allocator)) (local-port i64) (spec TunnelSpec)] (-> (slice (slice u8))))
```

`tunnel-spec` forwards to `remote-port` on the remote's own loopback
(`127.0.0.1`, resolved on the far side), with a 15 s ready timeout, 15 s
keepalives (`ServerAliveCountMax=3`, so a dead link is noticed in about 45 s),
and reconnect backoff from 500 ms doubling to 30 s.

Opening reserves a free loopback port, runs
`ssh … -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -L 127.0.0.1:LOCAL:HOST:PORT DEST`,
and reports ready only when the local port accepts a connection (ssh binds it
after authentication and forward setup). Otherwise the tunnel is torn down and
the failure carries ssh's own words:

```
ssh tunnel to nobody@box exited before the forwarded port accepted connections: nobody@box: Permission denied (publickey,password).
```

Call `tunnel-ensure!` before each use: it returns the live tunnel, reopens a
dead one, and while a failing destination's backoff has not expired returns
the previous failure verbatim instead of retrying. ssh's stderr is drained into
a bounded tail on every check, so a stream of failed channel opens cannot fill
the pipe and stall ssh. The proof covers the tunnel, not the service: a remote
port with nothing listening still yields an accepting local port whose
connections ssh closes immediately.

```coil
(let [(mut hub) (tunnel-new a (tunnel-spec (target "" "metaphysics" 0) 7420))]
  (match (tunnel-ensure! (mut hub))
    (TunnelReady [port] (use-api (tunnel-base-url a port)))
    (TunnelFailed [why] (report why)))
  (tunnel-free! (mut hub)))
```

`TunnelFailed` messages borrow the tunnel's buffer and stay valid until the
next open.

## Tests

`coil test` runs 16 offline tests: target parsing and rejection, rendering,
shell quoting, argument vectors, request validation, spawn failure, SIGPIPE
suppression, tunnel backoff, and failure caching.

`coil test --suite live` (opt-in, 15 tests) needs passwordless ssh to
`computer.jimmyhmiller.com`: `echo`, stdin passthrough (including 2 MiB
round-tripped through `cat` and a remote that stops reading after 5 bytes),
stderr and nonzero exit capture, output truncation, deadline termination,
unknown-host failure, reachability, shell-quoting round trip, stdin to a
PID-unique remote temp file, and tunnels to the remote sshd on its
`127.0.0.1:22` that must deliver the `SSH-2.0-` banner, including reopening
after the ssh child is killed.

## Platform notes

Errno values, `O_NONBLOCK`, and `SIG_BLOCK`/`SIG_SETMASK` differ between Linux
and Darwin; `ssh.platform/platform-pick` selects the right spelling for the
compilation target. `ssh.platform` compares with `primitive/code-eq` on purpose:
`coil lint` suggests `=`, but a macro that compares Code with `=` cannot
currently be compiled into the metaprogram engine, so that one lint warning is
expected.
