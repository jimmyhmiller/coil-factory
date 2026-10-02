#!/usr/bin/env python3
"""Probes: real Coil programs that import `live`, take edits over nREPL, and
are measured from Coil's own JIT trace (COIL_JIT_TRACE=1).

Each probe checks one thing every earlier attempt got wrong. The contract they
rely on is SPEC.md A3: `(import "live" :as live)`, `(live/start-nrepl! HOST
PORT)` writing `.nrepl-port`, and `eval` with one form per request.

Usage: probe.py WORKSPACE [PROBE ...]     (no names: every probe)
Prints one line per probe, `PASS name` or `FAIL name: why`, then details.
Exit status: 0 when every named probe passed, 1 otherwise.

This file lives outside the workspace on purpose: agents cannot change what
it measures.
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time

STARTUP_SECONDS = 900
STEP_SECONDS = 600

# ---------------------------------------------------------------------------
# bencode


def benc(x):
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode()
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(benc(i) for i in x) + b"e"
    if isinstance(x, dict):
        return b"d" + b"".join(benc(k) + benc(x[k]) for k in sorted(x)) + b"e"
    raise TypeError(x)


class Reader:
    def __init__(self, sock):
        self.sock, self.buf = sock, b""

    def need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("the nREPL server closed the connection")
            self.buf += chunk

    def take(self, n):
        self.need(n)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def value(self):
        self.need(1)
        c = self.buf[:1]
        if c == b"i":
            while b"e" not in self.buf:
                self.need(len(self.buf) + 1)
            end = self.buf.index(b"e")
            n = int(self.buf[1:end]); self.buf = self.buf[end + 1:]
            return n
        if c == b"l":
            self.take(1); out = []
            while True:
                self.need(1)
                if self.buf[:1] == b"e":
                    self.take(1); return out
                out.append(self.value())
        if c == b"d":
            self.take(1); out = {}
            while True:
                self.need(1)
                if self.buf[:1] == b"e":
                    self.take(1); return out
                k = self.value(); out[k if isinstance(k, str) else k.decode()] = self.value()
        while b":" not in self.buf:
            self.need(len(self.buf) + 1)
        colon = self.buf.index(b":")
        n = int(self.buf[:colon]); self.buf = self.buf[colon + 1:]
        raw = self.take(n)
        try:
            return raw.decode()
        except UnicodeDecodeError:
            return raw


class Nrepl:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=STEP_SECONDS)
        self.reader, self.n = Reader(self.sock), 0

    def eval(self, code):
        """Send one eval; collect replies until status has `done`."""
        self.n += 1
        rid = "p%d" % self.n
        self.sock.sendall(benc({"op": "eval", "code": code, "id": rid}))
        merged = {"status": [], "value": "", "err": "", "ex": "", "out": ""}
        while True:
            msg = self.reader.value()
            if msg.get("id") not in (None, rid):
                continue
            for k in ("value", "err", "ex", "out"):
                if k in msg:
                    merged[k] += str(msg[k])
            merged["status"] += list(msg.get("status", []))
            if "done" in merged["status"]:
                return merged


# ---------------------------------------------------------------------------
# a live program


class Program:
    """A probe program in its own project that depends on the workspace."""

    def __init__(self, workspace, module, source):
        self.dir = tempfile.mkdtemp(prefix="live-probe-")
        os.makedirs(os.path.join(self.dir, "src"))
        with open(os.path.join(self.dir, "Coil.toml"), "w") as f:
            f.write('[package]\nname = "probe"\nentry = "src/main.coil"\nsource-roots = ["src"]\n\n'
                    '[dependencies]\nlive = { path = "%s" }\n' % os.path.abspath(workspace))
        with open(os.path.join(self.dir, "src", "main.coil"), "w") as f:
            f.write(source)
        self.module = module
        self.lines, self.lock = [], threading.Lock()
        env = dict(os.environ, COIL_JIT_TRACE="1")
        self.proc = subprocess.Popen(["coil", "run"], cwd=self.dir, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for raw in self.proc.stdout:
            with self.lock:
                self.lines.append(raw.decode(errors="replace").rstrip("\n"))

    def mark(self):
        with self.lock:
            return len(self.lines)

    def since(self, mark):
        with self.lock:
            return list(self.lines[mark:])

    def port(self):
        path = os.path.join(self.dir, ".nrepl-port")
        deadline = time.time() + STARTUP_SECONDS
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("the program exited (status %s) before serving nREPL:\n%s"
                                   % (self.proc.returncode, "\n".join(self.since(0)[-40:])))
            if os.path.exists(path):
                text = open(path).read().strip()
                if text.isdigit():
                    return int(text)
            time.sleep(0.2)
        raise RuntimeError("no .nrepl-port after %ds" % STARTUP_SECONDS)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


def jit_work(lines, pattern):
    """The user functions (matching `pattern`) Coil checked and emitted, over
    every JIT submission (`jit-work begin` .. `jit-work end`) in `lines`."""
    checked, emitted, submissions, inside = set(), set(), 0, False
    rx = re.compile(r"^jit-work (begin|end|check|emit)\b ?(.*)$")
    for line in lines:
        m = rx.match(line)
        if not m:
            continue
        kind, rest = m.groups()
        if kind == "begin":
            submissions += 1; inside = True
        elif kind == "end":
            inside = False
        elif inside:
            for name in re.findall(pattern, rest):
                (checked if kind == "check" else emitted).add(name)
    return checked, emitted, submissions


def step(nrepl, program, code):
    mark = program.mark()
    reply = nrepl.eval(code)
    time.sleep(0.3)  # let the trace lines of this submission arrive
    return reply, program.since(mark)


class Fail(Exception):
    pass


def expect(cond, why):
    if not cond:
        raise Fail(why)


def accepted(reply):
    return "eval-error" not in reply["status"] and not reply["ex"]


# ---------------------------------------------------------------------------
# probes

# Every probe program serves nREPL and then idles until the driver kills it.
IDLE = ("(defn idle [] (-> i64)\n"
        "  (match (time/duration-millis 1000)\n"
        "    (Ok [second] (do (loop (time/sleep second)) 0))\n"
        "    (Err [e] (do (println \"probe: cannot make a one-second duration\") (os/_exit (cast i32 70)) 70))))\n\n")


def chain_program(n):
    fns = ["(defn scale-fn-000 [] (-> i64) 0)"]
    for i in range(1, n):
        fns.append("(defn scale-fn-%03d [] (-> i64) (+ (scale-fn-%03d) 1))" % (i, i - 1))
    return ("(module probe.scale)\n(import \"live\" :as live)\n(import \"coil.time\" :as time)\n(import \"coil.os\" :as os)\n" + IDLE + "\n"
            + "\n".join(fns)
            + "\n\n(defn main [] (-> i64)\n  (live/start-nrepl! \"127.0.0.1\" 0)\n"
              "  (idle))\n")


def probe_fn_body_scale(workspace):
    """A body edit compiles that one function, the same work at every size,
    and callers see the new body through the var (RULES 1, 3)."""
    work = {}
    for n in (5, 80, 400):
        program = Program(workspace, "probe.scale", chain_program(n))
        try:
            nrepl = Nrepl(program.port())
            reply, _ = step(nrepl, program, "(scale-fn-002)")
            expect(accepted(reply) and reply["value"].strip() == "2",
                   "n=%d: (scale-fn-002) before the edit gave %r" % (n, reply))
            reply, lines = step(nrepl, program, "(defn scale-fn-000 [] (-> i64) 1000)")
            expect(accepted(reply), "n=%d: the body edit was rejected: %s %s" % (n, reply["ex"], reply["err"]))
            checked, emitted, subs = jit_work(lines, r"scale-fn-(\d{3})")
            expect("000" in emitted,
                   "n=%d: Coil's trace never emitted scale-fn-000 for its own edit (emitted %s)" % (n, sorted(emitted)))
            expect(checked <= {"000"} and emitted <= {"000"},
                   "n=%d: editing scale-fn-000 also checked %s and emitted %s"
                   % (n, sorted(checked - {"000"}), sorted(emitted - {"000"})))
            work[n] = (len(checked), len(emitted), subs)
            reply, _ = step(nrepl, program, "(scale-fn-002)")
            expect(reply["value"].strip() == "1002",
                   "n=%d: after the edit (scale-fn-002) gave %r, not 1002" % (n, reply["value"]))
        finally:
            program.close()
    expect(len(set(work.values())) == 1,
           "the work for one edit depends on program size: %s (checked, emitted, submissions)" % work)


def probe_struct_field_add(workspace):
    """Adding a field recompiles no reader (RULES 3, SPEC A2)."""
    readers = "\n".join("(defn read-%03d [(r Rec)] (-> i64) (+ (.x r) %d))" % (i, i) for i in range(80))
    source = ("(module probe.rec)\n(import \"live\" :as live)\n(import \"coil.time\" :as time)\n(import \"coil.os\" :as os)\n" + IDLE + "\n"
              "(defstruct Rec [(x i64) (y i64)])\n" + readers +
              "\n\n(defn main [] (-> i64)\n  (live/start-nrepl! \"127.0.0.1\" 0)\n"
              "  (idle))\n")
    program = Program(workspace, "probe.rec", source)
    try:
        nrepl = Nrepl(program.port())
        reply, lines = step(nrepl, program, "(defstruct Rec [(x i64) (y i64) (z i64)])")
        expect(accepted(reply), "adding a field to a type with no values was rejected: %s %s" % (reply["ex"], reply["err"]))
        checked, emitted, _ = jit_work(lines, r"read-(\d{3})")
        expect(not checked and not emitted,
               "adding a field checked %d and emitted %d of the 80 readers" % (len(checked), len(emitted)))
        reply, _ = step(nrepl, program, "(read-003 (Rec :x 5 :y 6 :z 7))")
        expect(reply["value"].strip() == "8", "(read-003 (Rec :x 5 :y 6 :z 7)) gave %r, not 8" % (reply,))
    finally:
        program.close()


def plain_diagnostic(form):
    """What plain `coil check` says about `form`, after `in '<module>.<name>': `."""
    d = tempfile.mkdtemp(prefix="live-plain-")
    try:
        path = os.path.join(d, "plain.coil")
        with open(path, "w") as f:
            f.write("(module plain.diag)\n" + form + "\n")
        ran = subprocess.run(["coil", "check", path], capture_output=True, text=True)
        out = ran.stdout + ran.stderr
        m = re.search(r"error: (?:in|function) '[^']*': (.*)", out)
        if not m:
            raise RuntimeError("plain coil check gave no diagnostic for %s:\n%s" % (form, out))
        return m.group(1).strip()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def probe_diagnostic_verbatim(workspace):
    """A form that does not compile is rejected with Coil's own words (RULES 5)."""
    bad = "(defn bad-ret [] (-> i64) true)"
    coil_says = plain_diagnostic(bad)
    source = ("(module probe.diag)\n(import \"live\" :as live)\n(import \"coil.time\" :as time)\n(import \"coil.os\" :as os)\n" + IDLE + "\n"
              "(defn ok-ret [] (-> i64) 1)\n\n(defn main [] (-> i64)\n  (live/start-nrepl! \"127.0.0.1\" 0)\n"
              "  (idle))\n")
    program = Program(workspace, "probe.diag", source)
    try:
        nrepl = Nrepl(program.port())
        reply, _ = step(nrepl, program, bad)
        expect("eval-error" in reply["status"], "an ill-typed definition was accepted: %r" % (reply,))
        expect(reply["ex"] == "does-not-compile", "category %r, not does-not-compile" % reply["ex"])
        expect(coil_says in reply["err"],
               "the error is not Coil's.\n  Coil says: %s\n  live said: %s" % (coil_says, reply["err"]))
        reply, _ = step(nrepl, program, "(defn bad-ret [] (-> i64) 7)")
        expect(accepted(reply), "the corrected definition was rejected: %s" % reply["err"])
        reply, _ = step(nrepl, program, "(+ (bad-ret) (ok-ret))")
        expect(reply["value"].strip() == "8", "(+ (bad-ret) (ok-ret)) gave %r, not 8" % (reply,))
    finally:
        program.close()


def probe_ordinary_coil(workspace):
    """Ordinary Coil works: an ArrayList of the program's own struct, before
    and after an edit (RULES 4)."""
    source = ("(module probe.plain)\n(import \"live\" :as live)\n(import \"coil.time\" :as time)\n(import \"coil.os\" :as os)\n" + IDLE + ""
              "(import \"coil.alloc\" :use *)\n(import \"coil.arraylist\" :use *)\n\n"
              "(defstruct Player [(x i64) (y i64)])\n\n"
              "(defn team-y [] (-> i64)\n  (let [(mut l) (al-new [Player] (malloc-allocator))]\n"
              "    (push! (mut l) (Player :x 1 :y 2))\n    (push! (mut l) (Player :x 3 :y 4))\n"
              "    (+ (.y (get l 0)) (.y (get l 1)))))\n\n"
              "(defn main [] (-> i64)\n  (live/start-nrepl! \"127.0.0.1\" 0)\n"
              "  (idle))\n")
    program = Program(workspace, "probe.plain", source)
    try:
        nrepl = Nrepl(program.port())
        reply, _ = step(nrepl, program, "(team-y)")
        expect(reply["value"].strip() == "6", "(team-y) gave %r, not 6" % (reply,))
        edit = ("(defn team-y [] (-> i64)\n  (let [(mut l) (al-new [Player] (malloc-allocator))]\n"
                "    (push! (mut l) (Player :x 1 :y 20))\n    (+ (.y (get l 0)) (len l))))")
        reply, _ = step(nrepl, program, edit)
        expect(accepted(reply), "the edit was rejected: %s %s" % (reply["ex"], reply["err"]))
        reply, _ = step(nrepl, program, "(team-y)")
        expect(reply["value"].strip() == "21", "after the edit (team-y) gave %r, not 21" % (reply,))
    finally:
        program.close()


PROBES = {
    "fn-body-scale": probe_fn_body_scale,
    "struct-field-add": probe_struct_field_add,
    "diagnostic-verbatim": probe_diagnostic_verbatim,
    "ordinary-coil": probe_ordinary_coil,
}


def run(workspace, names):
    results = {}
    for name in names:
        try:
            PROBES[name](workspace)
            results[name] = (True, "")
        except Fail as e:
            results[name] = (False, str(e))
        except Exception as e:  # the program could not even be driven
            results[name] = (False, "%s: %s" % (type(e).__name__, e))
    return results


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    workspace, names = sys.argv[1], sys.argv[2:] or list(PROBES)
    unknown = [n for n in names if n not in PROBES]
    if unknown:
        print("unknown probe(s): %s; the probes are %s" % (", ".join(unknown), ", ".join(PROBES))); sys.exit(2)
    results = run(workspace, names)
    if "--json" in os.environ.get("PROBE_FORMAT", ""):
        print(json.dumps({n: {"ok": ok, "why": why} for n, (ok, why) in results.items()}))
    else:
        for name, (ok, why) in results.items():
            print(("PASS %s" % name) if ok else ("FAIL %s: %s" % (name, why.splitlines()[0] if why else "")))
            if not ok and len(why.splitlines()) > 1:
                print("  " + "\n  ".join(why.splitlines()[1:]))
    sys.exit(0 if all(ok for ok, _ in results.values()) else 1)


if __name__ == "__main__":
    main()
