import http.client
import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from tests.test_workbench import _TEMP


class HTTPTests(unittest.TestCase):
    def test_teaching_endpoints_and_artifact_boundaries(self):
        from workbench.server import make_server
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory(dir=_TEMP) as td:
            server = make_server(Runner(Path(td)), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def call(method, path, data=None, authorized=True):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                headers = {'Content-Type': 'application/json'}
                if authorized:
                    headers['X-Workbench-Token'] = server.token
                connection.request(method, path, json.dumps(data) if data is not None else None, headers)
                response = connection.getresponse()
                value = response.status, response.read()
                connection.close()
                return value
            try:
                code, body = call('GET', '/api/teaching')
                self.assertEqual(code, 200)
                self.assertNotIn('api_key', json.loads(body)['settings'])
                self.assertEqual(call('POST', '/api/teaching/create', {'title': 'demo'}, False)[0], 403)
                code, body = call('POST', '/api/teaching/create', {'title': 'demo'})
                self.assertEqual(code, 201)
                identifier = json.loads(body)['id']
                from unittest.mock import patch
                self.assertEqual(call('POST', '/api/teaching/open-source', {'id': identifier}, False)[0], 403)
                with patch('workbench.teaching.os.startfile') as opener:
                    self.assertEqual(call('POST', '/api/teaching/open-source', {'id': '../private'})[0], 400)
                    self.assertFalse(opener.called)
                    self.assertEqual(call('POST', '/api/teaching/open-source', {'id': identifier})[0], 200)
                    self.assertEqual(opener.call_args.args[0], str(server.teaching.directory(identifier) / 'source'))
                code, renamed = call('POST', '/api/teaching/rename', {'id': identifier, 'title': '计算器实验'})
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(renamed)['title'], '计算器实验')
                self.assertIn('source_path', json.loads(renamed))
                code, imported = call('POST', '/api/teaching/connection-import', {'config': {
                    'base_url': 'https://relay.example/v1', 'model': 'vendor/model', 'api_key': 'fixture'}})
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(imported)['api_key'], 'fixture')
                self.assertEqual(server.teaching.settings['provider_id'], 'deepseek', 'import is a preview, not a saved setting')
                self.assertEqual(call('POST', '/api/teaching/connection-import', {'config': '{invalid'})[0], 400)
                requirement = '较长的设计需求 ' * 12000
                with patch.object(server.teaching, 'send', return_value={'accepted': True}) as send:
                    code, response = call('POST', '/api/teaching/send', {'id': identifier, 'message': requirement})
                    self.assertEqual(code, 202)
                    self.assertEqual(send.call_args.args[1], requirement)
                self.assertEqual(call('GET', '/api/teaching/session?id=' + identifier)[0], 200)
                self.assertIn('flow', json.loads(call('GET', '/api/teaching/session?id=' + identifier)[1]))
                self.assertEqual(call('POST', '/api/teaching/program', {'id': identifier, 'confirmation': 'PROGRAM SRAM', 'board_confirmed': True}, False)[0], 403)
                self.assertEqual(call('POST', '/api/teaching/program', {'id': identifier, 'confirmation': 'PROGRAM SRAM', 'board_confirmed': True})[0], 400)
                self.assertEqual(call('POST', '/api/teaching/observation', {'id': identifier, 'passed': True, 'procedure': 'test', 'observed': 'ok'})[0], 400)
                self.assertEqual(call('GET', '/api/teaching/flow-file?id=' + identifier + '&stage=../../&run=20261023T000000_aaaaaaaa&file=project.json')[0], 400)
                self.assertEqual(call('GET', '/api/teaching/flow-file?id=' + identifier + '&stage=implementation&run=20261023T000000_aaaaaaaa&file=../../tool-config.json')[0], 400)
                self.assertEqual(call('GET', '/api/teaching/file?id=' + identifier + '&path=../settings.json')[0], 400)
                from workbench.agent_tools import ExperimentTools
                folder = server.teaching.directory(identifier)
                ExperimentTools(folder).call('write_file', {'path': 'scripts/flow.tcl', 'content': 'place\nroute\n'})
                self.assertEqual(json.loads(call('GET', '/api/teaching/file?id=' + identifier + '&path=scripts/flow.tcl')[1])['content'], 'place\nroute\n')
                run_id = '20261023T000000_aaaaaaaa'
                run = folder / 'fpga/implementation' / run_id
                (run / 'source/scripts').mkdir(parents=True)
                (run / 'source/scripts/flow.tcl').write_text('place\nroute\n')
                (run / 'td.log').write_text('one\ntwo\nthree\n')
                query = '&stage=implementation&run=' + run_id
                code, body = call('GET', '/api/teaching/read-run?id=' + identifier + query + '&file=td.log&max_lines=2')
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['content'], 'one\ntwo')
                self.assertTrue(json.loads(body)['has_more'])
                self.assertEqual(call('GET', '/api/teaching/read-run?id=' + identifier + query + '&file=../../tool-config.json')[0], 400)
                self.assertEqual(call('GET', '/api/teaching/read-run?id=' + identifier + query + '&file=td.log&max_lines=201')[0], 400)
                self.assertEqual(call('GET', '/api/teaching/flow-file?id=' + identifier + query + '&file=source/scripts/flow.tcl')[0], 200)
                self.assertEqual(call('GET', '/eda-view.js')[0], 200)
                self.assertEqual(call('GET', '/chat')[0], 200)
            finally:
                server.shutdown()
                server.server_close()
                if hasattr(server, 'teaching'):
                    server.teaching.close()
                thread.join()

    def test_rejected_post_accepts_a_segmented_request_body(self):
        from workbench.server import make_server
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory(dir=_TEMP) as td:
            server = make_server(Runner(Path(td)), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            try:
                client.putrequest("POST", "/api/jobs")
                client.putheader("Content-Length", "2")
                client.endheaders()
                # Headers and body need not arrive in the same TCP segment.
                # Closing before the body arrives can reset the Windows connection.
                client.sock.settimeout(.1)
                with self.assertRaises(socket.timeout):
                    client.sock.recv(1)
                client.sock.settimeout(5)
                client.send(b"{}")
                response = client.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("error", json.loads(response.read()))
            finally:
                client.close()
                server.shutdown()
                server.server_close()
                thread.join()

    def test_mutations_require_token_and_local_origin(self):
        self.assertIsNotNone(importlib.util.find_spec("workbench.server"), "web server missing")
        from workbench.server import make_server
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory(dir=_TEMP) as td:
            server = make_server(Runner(Path(td)), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            def call(method, path, headers=None):
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                client.request(method, path, body="{}" if method == "POST" else None, headers=headers or {})
                response = client.getresponse()
                result = response.status, response.read()
                client.close()
                return result
            try:
                code, body = call("GET", "/api/state")
                self.assertEqual(code, 200)
                token = json.loads(body)["token"]
                self.assertEqual(call("POST", "/api/jobs")[0], 403)
                self.assertEqual(call("POST", "/api/jobs", {"X-Workbench-Token": token,
                                                            "Origin": "https://untrusted.test"})[0], 403)
                self.assertEqual(call("POST", "/api/jobs", {"X-Workbench-Token": token,
                                                            "Content-Type": "application/json"})[0], 400)
                self.assertEqual(call("GET", "/api/state", {"Host": "untrusted.test"})[0], 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
