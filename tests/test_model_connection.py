import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.config import load_config
from workbench.teaching import TeachingManager
from workbench.opencode import agent_config


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = TeachingManager(self.root, load_config)
        self.addCleanup(self.manager.close)

    def relay(self, **extra):
        return dict(provider_id='relay', provider_name='My gateway', api_protocol='openai-compatible',
                    base_url='https://gateway.example/v1', model='org/model-v2', api_key='relay-unit-secret', **extra)

    def test_relay_configuration_is_used_by_opencode_without_saving_key(self):
        public = self.manager.configure(self.relay(context_tokens=200000, model_output_tokens=64000))
        self.assertEqual(public['provider_id'], 'relay')
        config = agent_config(self.root, public['model'], connection=public)
        self.assertEqual(config['model'], 'relay/org/model-v2')
        self.assertEqual(config['provider']['relay']['npm'], '@ai-sdk/openai-compatible')
        self.assertEqual(config['provider']['relay']['options']['baseURL'], 'https://gateway.example/v1')
        self.assertEqual(config['provider']['relay']['models']['org/model-v2']['limit']['output'], 64000)
        for content in (json.dumps(public), json.dumps(config), (self.root/'settings.json').read_text()):
            self.assertNotIn('relay-unit-secret', content)

    def test_endpoint_switch_never_sends_the_old_key_to_the_new_service(self):
        self.manager.configure({'api_key': 'original-unit-secret'})
        before = dict(self.manager.settings)
        update = self.relay(); update.pop('api_key')
        with self.assertRaisesRegex(ValueError, 'Key'):
            self.manager.configure(update)
        self.assertEqual(self.manager.settings, before)
        self.assertEqual(self.manager.key, 'original-unit-secret')

    def test_unknown_model_does_not_inherit_deepseek_capabilities(self):
        public = self.manager.configure(self.relay())
        self.assertIsNone(public['limits']['model_max_output_tokens'])
        self.assertIsNone(public['context_tokens'])
        config = agent_config(self.root, public['model'], connection=public)
        self.assertNotIn('limit', config['provider']['relay']['models']['org/model-v2'])

    def test_opencode_json_import_extracts_only_connection_and_secret(self):
        from workbench.model_connection import parse_connection_json, export_connection
        config = {'model':'micu/model-a', 'provider': {'micu': {'npm':'@ai-sdk/openai', 'name':'米醋',
                  'options':{'baseURL':'https://www.micuapi.ai/v1', 'apiKey':'imported-unit-secret',
                             'headers':{'User-Agent':'OpenCode RTL-Lab'}},
                  'models':{'model-a':{'limit':{'context':128000, 'output':8192}}}}}}
        fields = parse_connection_json(json.dumps(config))
        self.assertEqual(fields['api_protocol'], 'openai-responses')
        self.assertEqual(fields['api_key'], 'imported-unit-secret')
        public = self.manager.configure(fields)
        exported = export_connection(public)
        self.assertNotIn('imported-unit-secret', json.dumps(exported))
        self.assertEqual(exported['provider']['micu']['options']['headers']['User-Agent'], 'OpenCode RTL-Lab')
        self.assertEqual(exported['provider']['micu']['models']['model-a']['headers']['User-Agent'], 'OpenCode RTL-Lab')
        exported['provider']['micu']['models']['model-a']['headers'] = {'user-agent':'Model header wins'}
        self.assertEqual(parse_connection_json(exported)['api_headers']['User-Agent'], 'Model header wins')
        self.assertEqual(parse_connection_json(exported)['model'], 'model-a')

    def test_opencode_capacity_pairs_and_independent_output_cap(self):
        with self.assertRaisesRegex(ValueError, '同时填写'):
            self.manager.configure(self.relay(context_tokens=65536))
        public = self.manager.configure(self.relay(max_output_tokens=4096))
        config = agent_config(self.root, public['model'], 4096, connection=public)
        self.assertNotIn('limit', config['provider']['relay']['models']['org/model-v2'])

    def test_bad_urls_headers_and_ambiguous_json_fail_before_any_mutation(self):
        before = dict(self.manager.settings)
        for fields in ({'base_url':'file:///secret'}, {'base_url':'https://user:secret@example.com/v1'},
                       {'base_url':'https://example.com/v1?api_key=secret'},
                       {'api_headers':{'User-Agent':'value\r\nInjection: yes'}},
                       {'api_headers':{'User-Agent':'{file:C:/private/key.txt}'}},
                       {'model':'{env:PRIVATE_MODEL}'},
                       {'api_headers':{'Authorization':'Bearer secret'}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.manager.configure(fields)
        self.assertEqual(before, self.manager.settings)
        from workbench.model_connection import parse_connection_json
        for raw in ('not json', {'mcp':{'bad':{}}}, {'provider':{'a':{},'b':{}}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_connection_json(raw)

    def test_model_list_check_uses_selected_endpoint_and_headers(self):
        self.manager.configure(self.relay(api_headers={'User-Agent':'RTL-Lab'}))
        from io import BytesIO
        with patch('workbench.teaching.urllib.request.urlopen', return_value=BytesIO(b'{"data":[{"id":"org/model-v2"}]}')) as request:
            result = self.manager.check()
        self.assertTrue(result['selected_available'])
        self.assertEqual(request.call_args.args[0].full_url, 'https://gateway.example/v1/models')
        self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer relay-unit-secret')
        self.assertEqual(request.call_args.args[0].get_header('User-agent'), 'RTL-Lab')

    def test_responses_and_anthropic_use_their_native_adapters(self):
        for protocol, npm in [('openai-responses','@ai-sdk/openai'),('anthropic','@ai-sdk/anthropic')]:
            fields = self.relay(); fields['api_protocol'] = protocol
            public = self.manager.configure(fields)
            config = agent_config(self.root, public['model'], connection=public)
            self.assertEqual(config['provider']['relay']['npm'], npm)
        from workbench.model_connection import parse_connection_json
        self.assertEqual(parse_connection_json({'model':'openai/model-test', 'provider':{'openai':{
            'options':{'baseURL':'https://relay.example/v1'}}}})['api_protocol'], 'openai-responses')
