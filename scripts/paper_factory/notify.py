"""Send readiness notifications through the existing signal-cli UNIX JSON-RPC socket."""
import json
from pathlib import Path
import shlex
import socket
import time
import uuid


def environment(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        parts = shlex.split(value, comments=True)
        values[key.strip()] = " ".join(parts)
    return values


class SignalNotifier:
    def __init__(self, config):
        self.config = config

    def send(self, message):
        values = environment(self.config["env_file"])
        account = values.get("SIGNAL_PHONE_NUMBER")
        recipients = [s.strip() for s in values.get("ALLOWED_SENDERS", "").split(",") if s.strip()]
        if not account or len(recipients) != 1:
            raise ValueError("Signal notification requires the existing account and exactly one configured recipient")
        path = self.config.get("socket_path") or values.get("SIGNAL_SOCKET_PATH")
        if not path:
            raise ValueError("Signal socket is not configured")
        request_id = uuid.uuid4().hex
        request = {"jsonrpc": "2.0", "id": request_id, "method": "send",
                   "params": {"account": account, "recipient": recipients, "message": message}}
        deadline = time.monotonic() + 40
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(40)
            connection.connect(path)
            connection.sendall((json.dumps(request) + "\n").encode())
            with connection.makefile("rb") as stream:
                while time.monotonic() < deadline:
                    connection.settimeout(max(0.1, deadline - time.monotonic()))
                    line = stream.readline(65537)
                    if not line:
                        raise ConnectionError("Signal socket closed before acknowledging send")
                    if len(line) > 65536:
                        raise ValueError("Signal response exceeded size limit")
                    response = json.loads(line)
                    if response.get("id") != request_id:
                        continue
                    if response.get("error"):
                        # Do not expose private phone/account data from provider errors.
                        raise RuntimeError("Signal RPC failed, code " + str(response["error"].get("code")))
                    result = response.get("result")
                    if not isinstance(result, dict) or not result.get("timestamp"):
                        raise ValueError("Signal did not acknowledge a message timestamp")
                    for item in result.get("results", []):
                        if item.get("type") != "SUCCESS":
                            raise RuntimeError("Signal reported recipient delivery failure")
                    return {"timestamp": result["timestamp"]}
        raise TimeoutError("Signal send acknowledgment timed out")


def serve_daemon(env_file, executable):
    import os
    values = environment(env_file)
    path = values.get("SIGNAL_SOCKET_PATH")
    if not path:
        raise ValueError("Existing Signal setup has no socket path")
    # Single daemon owns the existing account store. on-connection preserves
    # compatibility with the bridge's receive events without discarding them
    # while no client is connected.
    os.execv(executable, [executable, "--scrub-log", "daemon", "--socket", path,
                         "--no-receive-stdout", "--receive-mode", "on-connection"])


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    if not args.daemon or not args.executable:
        parser.error("Use --daemon --executable PATH to manage the existing Signal socket")
    serve_daemon(args.env_file, args.executable)


if __name__ == "__main__":
    main()

