# http-app

A small HTTP/1.1 application layer for Coil: route dispatch, a threaded server
with streaming responses, Server-Sent Events, and JSON helpers.

| Namespace | What it provides |
| --- | --- |
| `http_app.router` | Routes, request helpers, responses (buffered or streaming), response encoding |
| `http_app.server` | A thread-per-connection server with timeouts, limits, and streaming |
| `http_app.sse` | An SSE response adapter over `sse.core`'s encoder |
| `http_app.json` | JSON responses, `{"error":{...}}` errors, string escaping |
| `http_app.native` | Internal host ABI: close-on-exec sockets, platform constants |

```toml
[dependencies]
http_app = { path = "../coil-factory/libraries/http-app" }
sse = { path = "../coil-factory/libraries/sse" }   # only if you name sse.core types
```

Requests are parsed by `coil.http.server`. Responses to clients are written
through `coil.socket`. There is no TLS and only IPv4 is supported. The server is
meant to sit on loopback, or behind something that terminates TLS.

## Example

A JSON route and an SSE route, both behind a bearer token:

```clojure
(import "http_app.json" :use *)
(import "http_app.router" :use *)
(import "http_app.server" :use *)
(import "http_app.sse" :use *)
(import "sse.core" :as sse)

(defn authorized? [(ctx RequestContext) (token (slice u8))] (-> bool)
  (match (bearer-token ctx)
    (Some [given] (constant-time-equal? given token))
    (None [] false)))

(defn unauthorized [(a (dyn Allocator))] (-> Response)
  (response-add-header a
                       (json-error a 401 "unauthorized" "bearer token required")
                       "WWW-Authenticate"
                       "Bearer"))

(defstruct RunHandler [(token (slice u8))])

(impl Handler RunHandler
  (handle [(self (ptr RunHandler)) (a (dyn Allocator)) (ctx RequestContext)]
          (-> Response)
          (if (not (authorized? ctx (.token self)))
              (unauthorized a)
              (let [(mut out) (sb-new a)]
                (sb-push-str! (mut out) "{\"run\":")
                (json-string-into! (mut out) (param ctx "run"))
                (sb-push-str! (mut out) "}")
                (json-response 200 (sb-finish! (mut out)))))))

;; "Give me the events after cursor X": a counter that ends after 3 events.
(defstruct Counter [(limit i64)])

(impl SseSource Counter
  (sse-next [(self (ptr Counter)) (a (dyn Allocator)) (cursor (slice u8))]
            (-> SseBatch)
            (let [seen (match (str-parse-int cursor) (Some [n] n) (None [] 0))]
              (if (>= seen (.limit self))
                  (SseEnd)
                  (let [(mut id) (sb-new a)
                        (mut events) (al-new [sse/SseEvent] a)]
                    (sb-push-i64! (mut id) (+ seen 1))
                    (push! [sse/SseEvent] (mut events)
                           (sse-event "tick" (sb-finish! (mut id)) "{}"))
                    (SseEvents (al-slice [sse/SseEvent] events))))))
  (sse-release [(self (ptr Counter)) (reason StreamEndReason)] (-> i64) 0))

(defstruct EventsHandler [(token (slice u8))])

(impl Handler EventsHandler
  (handle [(self (ptr EventsHandler)) (a (dyn Allocator)) (ctx RequestContext)]
          (-> Response)
          (if (not (authorized? ctx (.token self)))
              (unauthorized a)
              (let [(mut counter) (primitive/zeroed Counter)]
                (set! (.limit counter) 3)
                (sse-response a (box! a Counter (load counter))
                              (last-event-id ctx) (sse-options))))))

(defn main [] (-> i64)
  (let [router (primitive/alloc-stack Router)
        errors (primitive/alloc-stack JsonErrors)
        runs (primitive/alloc-stack RunHandler)
        events (primitive/alloc-stack EventsHandler)
        server (primitive/alloc-stack Server)]
    (set! router (router-new (malloc-allocator)))
    (router-set-errors! router errors)          ; 404/405/413/... as JSON
    (set! (.token runs) "s3cret")
    (set! (.token events) "s3cret")
    (route-add! router "GET" "/v1/runs/{run}" runs)
    (route-add! router "GET" "/v1/runs/{run}/events/stream" events)
    (match (server-listen! server (server-config) router)   ; 127.0.0.1, ephemeral port
      (Err [error] 1)
      (Ok [port]
          (do
            (server-serve! server)   ; blocks until server-stop! from another thread
            (server-close! server)
            0)))))
```

## `http_app.router`

### Routing

```clojure
(defstruct Route  [(method (slice u8)) (pattern (slice u8)) (handler (dyn Handler))])
(defstruct Router [(routes (ArrayList Route)) (allocator (dyn Allocator))
                   (errors (Option (dyn ErrorResponder)))])

(deftrait Handler [Self]
  (handle [(self (ptr Self)) (allocator (dyn Allocator)) (request RequestContext)]
          (-> Response)))

(defn router-new [(allocator (dyn Allocator))] (-> Router))
(defn router-free! [(router (ptr Router))] (-> i64))
(defn route-add! [(router (ptr Router)) (method (slice u8)) (pattern (slice u8))
                  (handler (dyn Handler))] (-> bool))      ; false: invalid or duplicate
(defn router-set-errors! [(router (ptr Router)) (responder (dyn ErrorResponder))] (-> i64))
(defn router-error [(router (ptr Router)) (allocator (dyn Allocator)) (status i64)
                    (code (slice u8)) (message (slice u8))] (-> Response))
(defn dispatch-with [(router (ptr Router)) (allocator (dyn Allocator))
                     (request http/Request)] (-> Response))
(defn dispatch [(router (ptr Router)) (request http/Request)] (-> Response))
```

A pattern is a literal path whose segments may be `{name}`. A named segment
matches exactly one nonempty segment. There are no wildcards. Matching compares
raw, not percent-decoded, path bytes. Routes are tried in registration order.

Dispatch results:

- A route matches the method and path: its handler's response.
- No pattern matches the path: **404** (`not_found`).
- A pattern matches the path under other methods: **405** (`method_not_allowed`)
  with an `Allow` header listing those methods, such as `Allow: GET, HEAD`.
  Before this library had a server, such requests got 404.

`HEAD` is not implied by `GET`, so register it explicitly if you want it. The
server omits the body of any response to a `HEAD` request.

`dispatch-with` allocates path parameters, and gives the handler an allocator,
from the `allocator` you pass. The server passes a per-connection arena.
`dispatch` uses the router's own allocator.

Handlers run concurrently on connection threads. Register every route before
serving, and make handlers thread-safe.

### Error responses

```clojure
(deftrait ErrorResponder [Self]
  (error-response [(self (ptr Self)) (allocator (dyn Allocator)) (status i64)
                   (code (slice u8)) (message (slice u8))] (-> Response)))
```

The router and server build their own responses through the router's responder.
If none is set, the body is plain text containing `message`. The codes are:

| Status | Code |
| --- | --- |
| 400 | `bad_request` |
| 404 | `not_found` |
| 405 | `method_not_allowed` |
| 408 | `request_timeout` |
| 413 | `payload_too_large` |
| 431 | `headers_too_large` |
| 500 | `internal_error` |
| 503 | `service_unavailable` |
| 505 | `http_version_not_supported` |

`http_app.json/JsonErrors` renders them as JSON.

### Request helpers

```clojure
(defstruct RequestContext [(request http/Request) (params (slice PathParam))
                           (path (slice u8)) (query (slice u8))])

(defn param [(ctx RequestContext) (name (slice u8))] (-> (slice u8)))          ; raw; "" if absent
(defn header [(ctx RequestContext) (name (slice u8))] (-> (slice u8)))         ; case-insensitive; "" if absent
(defn header-opt [(ctx RequestContext) (name (slice u8))] (-> (Option (slice u8))))
(defn body [(ctx RequestContext)] (-> (slice u8)))                             ; decoded (chunked removed)
(defn request-method [(ctx RequestContext)] (-> (slice u8)))
(defn query-param [(a (dyn Allocator)) (ctx RequestContext) (name (slice u8))] (-> QueryLookup))
(defsum QueryLookup (QueryMissing) (QueryFound [(value (slice u8))]) (QueryMalformed))
(defn percent-decode [(a (dyn Allocator)) (encoded (slice u8)) (plus-as-space bool)]
  (-> (Option (slice u8))))                                                    ; None: bad %XX
(defn bearer-token [(ctx RequestContext)] (-> (Option (slice u8))))            ; "Authorization: Bearer X"
(defn constant-time-equal? [(left (slice u8)) (right (slice u8))] (-> bool))
(defn ascii-ci-eq [(left (slice u8)) (right (slice u8))] (-> bool))
```

`query-param` follows `application/x-www-form-urlencoded` rules. Pairs are
separated by `&`, `+` means a space, and `%XX` is an escape. The first match
wins, and `k` with no `=` has an empty value. It returns `QueryMalformed` if a
key it has to compare has a bad escape, or if the matched value does. Decoded
bytes are returned as they are, without UTF-8 validation.

The slices in a context borrow the request. Do not keep them after the handler
returns.

### Responses

```clojure
(defstruct ResponseHeader [(name (slice u8)) (value (slice u8))])
(defstruct Response [(status i64) (content-type (slice u8)) (body (slice u8))
                     (headers (slice ResponseHeader))
                     (stream (Option (dyn ResponseStream)))])

(defn response [(status i64) (content-type (slice u8)) (body (slice u8))] (-> Response))
(defn response-with-headers [(status i64) (content-type (slice u8))
                             (headers (slice ResponseHeader)) (body (slice u8))] (-> Response))
(defn stream-response [(status i64) (content-type (slice u8))
                       (headers (slice ResponseHeader)) (stream (dyn ResponseStream))] (-> Response))
(defn response-add-header [(a (dyn Allocator)) (reply Response)
                           (name (slice u8)) (value (slice u8))] (-> Response))
(defn response-streaming? [(reply Response)] (-> bool))
(defn response-problem [(reply Response)] (-> (Option (slice u8))))   ; None = valid
(defn response-valid? [(reply Response)] (-> bool))
```

A response is valid when all of these hold:

- The status is between 200 and 599.
- Header names are tokens.
- No header value or content type contains CR, LF, or NUL.
- There is no `Content-Length`, `Transfer-Encoding`, `Connection`, or
  `Content-Type` header. The server owns framing, and the content type goes in
  its own field.
- A 204 or 304 response has no body and no stream.

An empty content type omits the header. The server answers an invalid handler
response with 500 `internal_error`.

### Streaming bodies

```clojure
(defsum StreamStep (StreamChunk [(bytes (slice u8))]) (StreamIdle) (StreamDone) (StreamFailed))
(defsum StreamEndReason (ReleaseCompleted) (ReleaseDisconnected) (ReleaseStopping) (ReleaseFailed))

(deftrait ResponseStream [Self]
  (stream-next [(self (ptr Self)) (allocator (dyn Allocator)) (idle-ms i64)] (-> StreamStep))
  (stream-release [(self (ptr Self)) (reason StreamEndReason)] (-> i64)))
```

- **`stream-next` must not block.** When nothing is ready, return `StreamIdle`.
  The server then waits up to `idle-poll-ms`, watching for a client disconnect
  or a server stop, and asks again. An empty chunk is treated as `StreamIdle`.
- **`idle-ms`** is the time since the server last wrote to this client. It lets
  the stream decide when to send keep-alive bytes.
- **`allocator`** is a step arena that is reset after every step. Chunk bytes
  should come from it. State that must outlive a step belongs in the stream
  object, allocated from the handler's allocator.
- **`stream-release` is called exactly once**, after the last `stream-next`.
  The reasons are:
  - `ReleaseCompleted`: the stream finished, or the request was `HEAD`.
  - `ReleaseDisconnected`: a write failed or timed out, or the client closed its
    side.
  - `ReleaseStopping`: `server-stop!` was called.
  - `ReleaseFailed`: the stream returned `StreamFailed`, or its response head
    was invalid, in which case the client gets 500.

  The concrete stream object must stay alive until release. Allocating it from
  the handler's allocator does that.

### Encoding

```clojure
(defsum BodyFraming (FramingLength [(length i64)]) (FramingChunked) (FramingClose))
(defn encode-response-head [(a (dyn Allocator)) (reply Response) (framing BodyFraming)] (-> (slice u8)))
(defn encode-response [(a (dyn Allocator)) (reply Response)] (-> (slice u8)))
(defn reason-phrase [(status i64)] (-> (slice u8)))
```

`encode-response` renders a complete buffered response: status line, headers,
`Connection: close`, `Content-Length`, then the body. It returns `""` for an
invalid response, which includes header-injection attempts. It aborts with a
clear message if given a streaming response, because that is a caller bug.

## `http_app.server`

```clojure
(defstruct ServerConfig
  [(host (slice u8))            ; "127.0.0.1"; dotted-quad IPv4 only
   (port i64)                   ; 0 = ephemeral
   (backlog i64)                ; 128
   (max-request-bytes i64)      ; 1 MiB; request line + headers + body; 1 KiB .. 8 MiB + 64 KiB
   (read-timeout-ms i64)        ; 10000; the whole request must arrive in this time
   (write-timeout-ms i64)       ; 30000; per wait for the client to drain its window
   (idle-poll-ms i64)           ; 250; how often an idle stream is asked again
   (max-connections i64)        ; 256; more concurrent connections get 503
   (thread-stack-bytes i64)])   ; 4 MiB per connection thread (0 = platform default)

(defsum ServerError
  (ServerInvalidConfig [(message (slice u8))])
  (ServerListenFailed [(code i64)])                         ; errno; 22 = bad host/port
  (ServerSetupFailed [(message (slice u8)) (code i64)])
  (ServerAcceptFailed [(code i64)]))

(defn server-config [] (-> ServerConfig))
(defn server-listen! [(server (ptr Server)) (config ServerConfig) (router (ptr Router))]
  (-> (Result i64 ServerError)))                             ; Ok bound port
(defn server-serve! [(server (ptr Server))] (-> (Result i64 ServerError)))   ; Ok connections accepted
(defn server-start! [(server (ptr Server))] (-> (Result i64 ServerError)))   ; serve on a new thread
(defn server-wait! [(server (ptr Server))] (-> (Result i64 ServerError)))    ; join it
(defn server-stop! [(server (ptr Server))] (-> i64))                         ; any thread, idempotent
(defn server-close! [(server (ptr Server))] (-> i64))
(defn server-port [(server (ptr Server))] (-> i64))
(defn server-listener-fd [(server (ptr Server))] (-> i32))
(defn server-active-connections [(server (ptr Server))] (-> i64))
(defn server-accepted-connections [(server (ptr Server))] (-> i64))
```

### Lifecycle

`Server` storage belongs to the caller and must not move. The sequence is:

1. `server-listen!`
2. Either `server-serve!`, which blocks, or `server-start!` followed later by
   `server-wait!`.
3. `server-close!`

`server-stop!` has these effects:

- The accept loop exits.
- Request reads in progress are abandoned.
- Every open stream is released with `ReleaseStopping` at its next step. Idle
  streams are woken immediately, and chunked streams get their terminating
  chunk.

`server-serve!` returns only after every connection thread has finished. After
that it is safe to free the router and handlers. `server-close!` stops the
server, joins a background serve thread, and releases the listener, stop pipe,
and locks. It is idempotent, and it does nothing after a failed `server-listen!`.

### Sockets

- The listener has `SO_REUSEADDR`, so a restarted daemon can rebind right away.
- The listener, every accepted connection, and the internal stop pipe are
  close-on-exec, so child processes never inherit them.
  - On Linux this is atomic: `SOCK_CLOEXEC` for the listener and `accept4` for
    connections.
  - Darwin has neither, so `fcntl` runs immediately after creation. A child
    spawned by another thread in that window can still inherit the descriptor.
- Connections are non-blocking. Every wait uses `poll` and also watches the stop
  pipe.
- Writes never raise SIGPIPE: `MSG_NOSIGNAL` on Linux, `SO_NOSIGPIPE` on Darwin.
  A failed write ends the connection.

### Requests

- Each connection serves one request, and every response carries
  `Connection: close`. There is no keep-alive or pipelining, and extra bytes
  after the request are ignored.
- The request is buffered until `coil.http.server/parse-request` accepts it.
  Parsing starts only once the header block is complete.
  - A single `Content-Length` lets the server wait for exactly that many body
    bytes, and reject an oversized body with 413 before reading it.
  - Chunked bodies are parsed again after each read.
- `Expect: 100-continue` from an HTTP/1.1 client is answered with
  `100 Continue`, unless the request is already rejected.
- Error statuses:
  - 408: the request is not complete within `read-timeout-ms`.
  - 413: the request exceeds `max-request-bytes`.
  - 431: the header block exceeds 64 KiB.
  - 400: the request is malformed.
  - 505: the version is not HTTP/1.0 or HTTP/1.1.
  - 503: the connection is over `max-connections`. This answer comes from the
    accept thread, which does not read the request.
- After an error, or after any complete response, the server half-closes the
  connection and briefly drains client bytes before closing. This keeps the
  kernel from resetting the connection and discarding the response.
  - Normal connections drain for up to 1 s.
  - A 503 rejection drains for up to 100 ms, because it holds up the accept
    thread.

### Responses

- **Buffered** responses get a `Content-Length`.
- **Streaming** responses to HTTP/1.1 use `Transfer-Encoding: chunked`, and a
  clean end sends the zero-length chunk. `StreamFailed` closes the connection
  without that chunk, so the client can see the body was truncated.
- **Streaming** responses to HTTP/1.0 are delimited by closing the connection,
  so a clean end and a failure look the same to the client.
- The latency for a new event on an idle stream is at most `idle-poll-ms`.

### Memory

Each connection gets two `coil.scratch` arenas, both closed when the connection
ends:

- A **connection arena** for the request, handler output, and stream objects.
- A **step arena** for stream chunks, recycled after every step.

A 2,000-connection soak and a 20 MB, 20,000-event stream left peak RSS flat.

## `http_app.sse`

```clojure
(defsum SseBatch (SseEvents [(events (slice sse/SseEvent))]) (SseNothing) (SseEnd) (SseError))

(deftrait SseSource [Self]
  (sse-next [(self (ptr Self)) (allocator (dyn Allocator)) (cursor (slice u8))] (-> SseBatch))
  (sse-release [(self (ptr Self)) (reason StreamEndReason)] (-> i64)))

(defstruct SseOptions [(keep-alive-ms i64) (retry-ms i64)])   ; defaults 15000, -1 (no retry hint)
(defn sse-options [] (-> SseOptions))
(defn sse-response [(a (dyn Allocator)) (source (dyn SseSource)) (cursor (slice u8))
                    (options SseOptions)] (-> Response))
(defn sse-event [(name (slice u8)) (id (slice u8)) (data (slice u8))] (-> sse/SseEvent))
(defn last-event-id [(ctx RequestContext)] (-> (slice u8)))
```

You implement "give me the events after cursor X". `sse-response` does the rest:

- It keeps the cursor, starting from the `cursor` you pass. Usually that is
  `last-event-id`.
- After each batch, the cursor becomes the id of the last event that has one.
- Events are framed with `sse.core/sse-encode`, one chunk per batch.
- If `retry-ms >= 0`, a `retry:` hint is sent once before the first event.
- When the source has nothing and the connection has been quiet for
  `keep-alive-ms`, it sends a `: keep-alive` comment.
- Headers:
  - `Content-Type: text/event-stream; charset=utf-8`
  - `Cache-Control: no-cache`
  - `X-Accel-Buffering: no`

An event the encoder cannot frame fails the stream with `ReleaseFailed`. That
covers a newline in the name or id, a NUL in the id, and a carriage return in
the data. Carriage returns are rejected because SSE treats CR as a line break,
and `sse.core` would otherwise corrupt the event.

`sse-release` is forwarded from the stream's release, exactly once.

## `http_app.json`

```clojure
(defn json-response [(status i64) (body (slice u8))] (-> Response))    ; body is JSON text, borrowed
(defn json-error [(a (dyn Allocator)) (status i64) (code (slice u8)) (message (slice u8))] (-> Response))
(defn json-error-body [(a (dyn Allocator)) (code (slice u8)) (message (slice u8))] (-> (slice u8)))
(defn json-string-into! [(out (mut StrBuf)) (value (slice u8))] (-> i64))  ; appends "…" escaped
(defn json-quote [(a (dyn Allocator)) (value (slice u8))] (-> (slice u8)))
(defstruct JsonErrors [(reserved u8)])                                  ; impl ErrorResponder
```

`json-error` produces `{"error":{"code":"…","message":"…"}}`. Strings are
escaped as follows:

- `"` and `\` are escaped.
- Control characters use `\n`, `\r`, `\t`, `\b`, `\f`, or `\u00XX`.
- Bytes that are not well-formed UTF-8 become `�`, one per bad byte, so the
  output is always valid JSON.

## Development

```sh
coil fmt --check src/*.coil tests/*.coil
coil lint
coil test
```

The server tests start real servers on ephemeral ports and drive them with
`coil.http.client`, both `request` and `request-stream`, and with raw sockets.
