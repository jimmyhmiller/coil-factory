"""Run after coil build: python3 -m unittest discover -s tests/live -p test_project_actions.py -v"""
import concurrent.futures
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import urllib.request
import urllib.error
import subprocess
import tempfile
import time
import unittest

BINARY = Path(__file__).resolve().parents[2] / 'build/release/factory'


class MCP:
    def __init__(self, home, project, scope='assistant', command=None):
        self.process = subprocess.Popen(
            command or [str(BINARY), '_mcp-harness', str(home / scope), 'assistant',
                        '--home', str(home), '--project', str(project)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0)
        self.sequence = 0
        self.buffer = b''
        self.request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                   'clientInfo': {'name': 'actions-test', 'version': '1'}})

    def request(self, method, params):
        self.sequence += 1
        message = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method, 'params': params}
        self.process.stdin.write(json.dumps(message).encode() + b'\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 15
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if b'\n' in self.buffer:
                    line, self.buffer = self.buffer.split(b'\n', 1)
                    response = json.loads(line)
                    if response.get('id') == self.sequence:
                        if 'error' in response:
                            raise AssertionError(response['error'])
                        return response['result']
                else:
                    if selector.select(max(0, deadline - time.monotonic())):
                        chunk = os.read(self.process.stdout.fileno(), 65536)
                        if not chunk:
                            raise AssertionError(f'MCP exited: {self.process.stderr.read().decode()}')
                        self.buffer += chunk
        raise AssertionError(f'Timeout for {method}')

    def tool(self, name, arguments=None, error=False):
        result = self.request('tools/call', {'name': name, 'arguments': arguments or {}})
        text = '\n'.join(c.get('text', '') for c in result.get('content', []))
        if error:
            assert result.get('isError'), text
            return text
        assert not result.get('isError'), text
        return json.loads(text)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()
        self.process.stderr.close()


class ProjectActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='factory-actions-')
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.project = self.root / 'project'
        self.home.mkdir()
        self.project.mkdir()
        self.clients = []
        self.runs = []
        self.client = self.connect()

    def connect(self, scope='assistant', project=None):
        client = MCP(self.home, project or self.project, scope)
        self.clients.append(client)
        return client

    def tearDown(self):
        for client, run in self.runs:
            try:
                client.tool('project_action_stop', {'run_id': run})
                self.wait(client, run, terminal=True)
            except (AssertionError, BrokenPipeError, ValueError):
                pass
        for client in self.clients:
            client.close()
        self.temp.cleanup()

    def define(self, command, ident='dev', client=None, **fields):
        client = client or self.client
        doc = client.tool('project_actions_list')
        return client.tool('project_action_set', {
            'expected_revision': doc['token'],
            'action': {'id': ident, 'label': 'Run application', 'command': command, **fields}})

    def start(self, client=None, ident='dev'):
        client = client or self.client
        doc = client.tool('project_actions_list')
        run = client.tool('project_action_start', {'action_id': ident, 'expected_revision': doc['token']})
        self.runs.append((client, run['run_id']))
        return run

    def wait(self, client, run, terminal=False):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.tool('project_action_status', {'run_id': run})
            if status['state'] in (('exited', 'failed', 'stopped') if terminal else ('running', 'exited', 'failed', 'stopped')):
                return status
            time.sleep(.05)
        self.fail(f'Run did not settle: {status}')

    def test_real_tools_and_success_failure_environment_and_workdir(self):
        tools = {t['name'] for t in self.client.request('tools/list', {})['tools']}
        self.assertTrue({'project_actions_list', 'project_action_set', 'project_action_remove',
                         'project_action_start', 'project_action_status', 'project_action_logs',
                         'project_action_stop'} <= tools)
        (self.project / 'subdir').mkdir()
        self.define('printf "%s\\n" "$ACTION_VALUE"; pwd; echo error-output >&2',
                    cwd='subdir', env={'ACTION_VALUE': "spaces; 'quotes' $(not-executed)"})
        self.assertFalse((self.project / 'subdir' / 'output').exists())
        run = self.start()
        status = self.wait(self.client, run['run_id'], terminal=True)
        self.assertEqual(status['state'], 'exited')
        self.assertEqual(status['exit_code'], 0)
        log = self.client.tool('project_action_logs', {'run_id': run['run_id']})
        self.assertIn("spaces; 'quotes' $(not-executed)", log['output'])
        self.assertIn(str(self.project / 'subdir'), log['output'])
        self.assertIn('error-output', log['output'])
        self.define('exit 7', order=10)
        self.define('true', ident='first', order=-1)
        self.assertEqual([a['id'] for a in self.client.tool('project_actions_list')['actions']], ['first', 'dev'])
        run = self.start()
        self.assertEqual(self.wait(self.client, run['run_id'], terminal=True)['exit_code'], 7)

    def test_concurrent_start_shared_state_survives_client_close_and_stale_stop(self):
        self.define('echo ready; sleep 60')
        other = self.connect('other-conversation')
        token = self.client.tool('project_actions_list')['token']
        args = {'action_id': 'dev', 'expected_revision': token}
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(c.tool, 'project_action_start', args) for c in (self.client, other)]
            runs = [f.result() for f in futures]
        self.assertEqual(runs[0]['run_id'], runs[1]['run_id'])
        old = runs[0]['run_id']
        self.runs.append((other, old))
        status = self.wait(other, old)
        parent = subprocess.run(['ps', '-o', 'ppid=', '-p', str(status['supervisor_pid'])],
                                capture_output=True, text=True, check=True)
        self.assertEqual(int(parent.stdout), 1, 'Supervisor must outlive harness descendant cleanup')
        self.client.close()
        self.assertEqual(other.tool('project_actions_list')['actions'][0]['run']['run_id'], old)
        other.tool('project_action_stop', {'run_id': old})
        self.assertEqual(self.wait(other, old, terminal=True)['state'], 'stopped')
        new = self.start(other)['run_id']
        self.wait(other, new)
        other.tool('project_action_stop', {'run_id': old})
        self.assertEqual(other.tool('project_action_status', {'run_id': new})['state'], 'running')

    def test_stop_cleans_process_tree_and_preserves_running_definition(self):
        self.define("sh -c 'sleep 60 & echo $! > grandchild.pid; wait' & echo $! > child.pid; echo ready; wait")
        run = self.start()['run_id']
        self.wait(self.client, run)
        pidfile = self.project / 'child.pid'
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        pid = int(pidfile.read_text())
        grandchild = int((self.project / 'grandchild.pid').read_text())
        self.define('echo changed')
        status = self.client.tool('project_action_status', {'run_id': run})
        self.assertIn('sleep 60', status['action']['command'])
        token = self.client.tool('project_actions_list')['token']
        self.client.tool('project_action_remove', {'action_id': 'dev', 'expected_revision': token}, error=True)
        self.client.tool('project_action_stop', {'run_id': run})
        self.wait(self.client, run, terminal=True)
        for descendant in (pid, grandchild):
            result = subprocess.run(['ps', '-o', 'stat=', '-p', str(descendant)], capture_output=True, text=True)
            self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith('Z'), result.stdout)

    def test_content_conflict_malformed_configuration_and_project_isolation(self):
        self.define('echo first')
        stale = self.client.tool('project_actions_list')['token']
        path = self.project / '.factory/actions.json'
        config = json.loads(path.read_text())
        config['actions'][0]['command'] = 'echo manually-edited'
        path.write_text(json.dumps(config))
        self.client.tool('project_action_start', {'action_id': 'dev', 'expected_revision': stale}, error=True)
        self.client.tool('project_action_set', {'expected_revision': stale,
            'action': {'id': 'dev', 'label': 'stale', 'command': 'true'}}, error=True)
        other_project = self.root / 'other-worktree'
        other_project.mkdir()
        other = self.connect('other', other_project)
        self.assertEqual(other.tool('project_actions_list')['actions'], [])
        self.define('sleep 60', client=other)
        run = self.start(other)['run_id']
        self.client.tool('project_action_status', {'run_id': run}, error=True)
        path.write_text('')
        self.assertIn('error', self.client.tool('project_actions_list'))
        self.assertEqual(path.read_text(), '')

    def test_bounded_logs_utf8_and_cursor(self):
        self.define("i=0; while [ $i -lt 18000 ]; do printf '%0128d λ🙂\\n' $i; i=$((i+1)); done")
        run = self.start()['run_id']
        self.wait(self.client, run, terminal=True)
        log = self.client.tool('project_action_logs', {'run_id': run, 'limit': 65536})
        self.assertTrue(log['truncated'])
        self.assertLessEqual(len(log['output'].encode()), 65536)
        cursor = log['next_cursor']
        following = self.client.tool('project_action_logs', {'run_id': run, 'cursor': cursor, 'limit': 500})
        self.assertGreater(following['next_cursor'], cursor)
        self.assertGreater(log['total_bytes'], 1048576)

    def test_standalone_agent_bridge_uses_site_runtime_not_agent_directory(self):
        agent_home = self.root / 'agent'
        agent_home.mkdir()
        agent_file = agent_home / 'runner.md'
        agent_file.write_text('---\nname: runner\ntools: files, shell\n---\n\nUse the tools to work on the project.\n')
        input_file = agent_home / 'input.txt'
        input_file.write_text('test')
        client = MCP(self.home, self.project, command=[str(BINARY), '_mcp-agent',
            '--agent', str(agent_file), '--input', str(input_file),
            '--results', str(agent_home / 'results.jsonl'), '--error', str(agent_home / 'error'),
            '--workdir', str(self.project), '--home', str(agent_home),
            '--site-home', str(self.home), '--isolation', 'none'])
        self.clients.append(client)
        self.define('sleep 60', client=client)
        run = self.start(client)['run_id']
        self.wait(client, run)
        shared = self.client.tool('project_actions_list')['actions'][0]['run']
        self.assertEqual(shared['run_id'], run)
        self.assertEqual(shared['state'], 'running')
        self.assertFalse((agent_home / 'actions').exists())

    def test_site_api_binds_execution_to_the_run_workspace(self):
        with socket.socket() as socket_handle:
            socket_handle.bind(('127.0.0.1', 0))
            port = socket_handle.getsockname()[1]
        (self.home / 'site.json').write_text(json.dumps({'port': port, 'name': 'actions-test', 'isolation': 'none'}))
        (self.home / 'token').write_text('action-test-token')
        run_dir = self.home / 'runs' / 'finished-fixture'
        workspace = run_dir / 'workspace'
        workspace.mkdir(parents=True)
        records = [{'kind': 'run.created', 't': 1, 'stations': ['build']},
                   {'kind': 'run.finished', 't': 2, 'status': 'succeeded'}]
        (run_dir / 'events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        daemon = subprocess.Popen([str(BINARY), 'daemon'], env={**os.environ, 'FACTORY_HOME': str(self.home)},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f'http://127.0.0.1:{port}'
        def request(kind, args=None, agent='build', authenticated=True):
            data = json.dumps({'kind': kind, 'args': args or {}}).encode()
            headers = {'Content-Type': 'application/json'}
            if authenticated:
                headers['Authorization'] = 'Bearer action-test-token'
            req = urllib.request.Request(base + f'/v1/runs/finished-fixture/agents/{agent}/actions',
                                         data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as reply:
                return json.load(reply)
        action_run = None
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    doc = request(1)
                    break
                except urllib.error.URLError:
                    if time.monotonic() > deadline:
                        self.fail('Site API did not become ready')
                    time.sleep(.05)
            with self.assertRaises(urllib.error.HTTPError) as unauthenticated:
                request(1, authenticated=False)
            self.assertEqual(unauthenticated.exception.code, 401)
            unauthenticated.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as unknown:
                request(1, agent='unknown')
            self.assertEqual(unknown.exception.code, 404)
            unknown.exception.close()
            request(2, {'expected_revision': doc['token'], 'action': {
                'id': 'app', 'label': 'App', 'command': 'pwd; echo started > marker; sleep 60'}})
            token = request(1)['token']
            action_run = request(4, {'expected_revision': token, 'action_id': 'app'})['run_id']
            deadline = time.monotonic() + 5
            while not (workspace / 'marker').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue((workspace / 'marker').exists())
            self.assertFalse((self.project / 'marker').exists())
            request(7, {'run_id': action_run})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = request(5, {'run_id': action_run})
                if status['state'] == 'stopped':
                    break
                time.sleep(.05)
            self.assertEqual(status['state'], 'stopped')
        finally:
            if action_run:
                try:
                    request(7, {'run_id': action_run})
                except urllib.error.URLError:
                    pass
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait()
            daemon.stderr.close()

    def test_supervisor_loss_reports_interrupted_without_signalling_arbitrary_pid(self):
        self.define('echo $$ > group.pid; echo ready; sleep 60')
        run = self.start()['run_id']
        status = self.wait(self.client, run)
        group = int((self.project / 'group.pid').read_text())
        try:
            os.kill(status['supervisor_pid'], signal.SIGKILL)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = self.client.tool('project_action_status', {'run_id': run})
                if status['state'] == 'interrupted':
                    break
                time.sleep(.05)
            self.assertEqual(status['state'], 'interrupted')
            self.client.tool('project_action_stop', {'run_id': run}, error=True)
        finally:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == '__main__':
    unittest.main()
