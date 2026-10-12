import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from paper_factory.notify import SignalNotifier, environment


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = self.root / "env"
        self.sock = self.root / "rpc.sock"
        self.env.write_text('SIGNAL_PHONE_NUMBER="+15550000001"\nALLOWED_SENDERS="+15550000002"\nSIGNAL_SOCKET_PATH=' + str(self.sock) + '\n')
        self.errors = []
        self.request = None

    def tearDown(self):
        self.temp.cleanup()

    def server(self, make_response):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.sock))
        listener.listen(1)
        listener.settimeout(2)
        def worker():
            try:
                with listener:
                    conn, _ = listener.accept()
                    with conn, conn.makefile("rb") as stream:
                        self.request = json.loads(stream.readline())
                        data = (json.dumps(make_response(self.request)) + "\n").encode()
                        # Exercise partial socket reads.
                        conn.sendall(data[:5])
                        conn.sendall(data[5:])
            except Exception as exc:
                self.errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        return thread

    def test_rpc_send_uses_existing_account_and_only_allowlisted_recipient(self):
        thread = self.server(lambda r: {"id":r["id"],"result":{"timestamp":1234,"results":[{"type":"SUCCESS"}]}})
        receipt = SignalNotifier({"env_file":str(self.env)}).send("Tutorial ready")
        thread.join(3)
        self.assertFalse(self.errors)
        self.assertEqual(receipt,{"timestamp":1234})
        self.assertEqual(self.request["method"],"send")
        self.assertEqual(self.request["params"]["recipient"],["+15550000002"])
        self.assertEqual(self.request["params"]["account"],"+15550000001")

    def test_rpc_errors_do_not_expose_account_details(self):
        thread = self.server(lambda r: {"id":r["id"],"error":{"code":-1,"message":"+15550000001 not registered"}})
        with self.assertRaisesRegex(RuntimeError, "code -1") as caught:
            SignalNotifier({"env_file":str(self.env)}).send("Tutorial ready")
        thread.join(3)
        self.assertNotIn("+1555",str(caught.exception))

    def test_recipient_failure_is_not_counted_as_sent(self):
        thread = self.server(lambda r: {"id":r["id"],"result":{"timestamp":1234,"results":[{"type":"UNREGISTERED_FAILURE"}]}})
        with self.assertRaisesRegex(RuntimeError,"recipient delivery failure"):
            SignalNotifier({"env_file":str(self.env)}).send("Tutorial ready")
        thread.join(3)

    def test_missing_acknowledgment_is_not_counted_as_sent(self):
        thread = self.server(lambda r: {"id":r["id"],"result":{}})
        with self.assertRaisesRegex(ValueError,"timestamp"):
            SignalNotifier({"env_file":str(self.env)}).send("Tutorial ready")
        thread.join(3)

    def test_multiple_recipient_allowlist_requires_explicit_configuration(self):
        self.env.write_text('SIGNAL_PHONE_NUMBER=+15550000001\nALLOWED_SENDERS=+15550000002,+15550000003\n')
        with self.assertRaisesRegex(ValueError,"exactly one"):
            SignalNotifier({"env_file":str(self.env)}).send("Tutorial ready")

    def test_environment_parser_handles_quotes_and_comments_without_executing(self):
        self.env.write_text('export KEY="quoted value" # comment\nEXPR="\u0024(command)"\n')
        self.assertEqual(environment(self.env), {"KEY":"quoted value","EXPR":"\u0024(command)"})


if __name__ == "__main__":
    unittest.main()

