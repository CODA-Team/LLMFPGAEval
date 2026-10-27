import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.config import load_config
from workbench.teaching import TeachingManager
from workbench.opencode import agent_config

CATALOG = {
    'GLM-5.3-Flash': {'provider_id':'micuapi','model_id':'glm-5.3-flash','base_url':'https://www.micuapi.ai/v1',
        'api_key_env':'MICUAPI_API_KEY','npm':'@ai-sdk/openai-compatible','usage_adapter':'openai-chat-v1',
        'user_agent':'Mozilla/5.0','settings':{}},
    'DeepSeek-Flash': {'provider_id':'deepseek-official','model_id':'deepseek-flash','base_url':'https://api.deepseek.com',
        'api_key_env':'DEEPSEEK_API_KEY','npm':'@ai-sdk/openai-compatible','usage_adapter':'deepseek-v1',
        'settings':{'reasoningEffort':'low','max_tokens':4096}}
}


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = TeachingManager(self.root, load_config)
        self.addCleanup(self.manager.close)

    def test_exact_user_json_is_saved_as_catalog_and_converted_for_opencode(self):
        with patch.dict(os.environ, {'MICUAPI_API_KEY':'micu-test-key','DEEPSEEK_API_KEY':'deepseek-test-key'}):
            public = self.manager.configure({'model_config':json.dumps(CATALOG),'active_profile':'GLM-5.3-Flash'})
            self.assertEqual(public['model_config'], CATALOG)
            self.assertEqual(self.manager.key, 'micu-test-key')
            self.assertEqual(public['profile_credentials']['MICUAPI_API_KEY']['source'], 'environment')
            generated = agent_config(self.root, public['model'], connection=public)
            self.assertEqual(generated['model'], 'micuapi/glm-5.3-flash')
            provider = generated['provider']['micuapi']
            self.assertEqual(provider['options']['apiKey'], '{env:MICUAPI_API_KEY}')
            self.assertEqual(provider['models']['glm-5.3-flash']['headers']['User-Agent'], 'Mozilla/5.0')
            switched = self.manager.configure({'active_profile':'DeepSeek-Flash'})
            self.assertEqual(self.manager.key, 'deepseek-test-key')
            self.assertEqual(switched['max_output_tokens'],4096)
            generated = agent_config(self.root, switched['model'], connection=switched)
            self.assertEqual(generated['provider']['deepseek-official']['models']['deepseek-flash']['options']['reasoningEffort'],'low')
            self.assertNotIn('usage_adapter', json.dumps(generated))
            self.assertNotIn('deepseek-test-key',json.dumps(switched))
            self.assertNotIn('micu-test-key',(self.root/'settings.json').read_text(encoding='utf-8'))

    def test_saved_keys_follow_env_names_and_survive_model_switch_and_restart(self):
        with patch.dict(os.environ, {}, clear=True):
            self.manager.configure({'model_config':CATALOG,'active_profile':'GLM-5.3-Flash','api_key':'micu-saved-test','remember_key':True})
            self.manager.configure({'active_profile':'DeepSeek-Flash','api_key':'deepseek-saved-test','remember_key':True})
            self.manager.close()
            again = TeachingManager(self.root,load_config)
            self.addCleanup(again.close)
            self.assertEqual(again.key,'deepseek-saved-test')
            public = again.configure({'active_profile':'GLM-5.3-Flash'})
            self.assertEqual(again.key,'micu-saved-test')
            self.assertTrue(public['key_saved'])
            again.configure({'clear_key':True})
            self.assertFalse(again.key)
            again.configure({'active_profile':'DeepSeek-Flash'})
            self.assertEqual(again.key,'deepseek-saved-test')
            self.assertEqual(again.public_settings()['model_config'],CATALOG)

    def test_legacy_saved_deepseek_key_is_available_after_json_migration(self):
        self.manager.configure({'api_key':'legacy-key-test','remember_key':True})
        before = (self.root/'private/deepseek.dpapi').read_bytes()
        self.manager.close()
        again = TeachingManager(self.root,load_config)
        self.addCleanup(again.close)
        self.assertEqual((self.root/'private/deepseek.dpapi').read_bytes(),before)
        self.assertTrue(again.public_settings()['model_config'])
        public = again.configure({'model_config':CATALOG,'active_profile':'DeepSeek-Flash'})
        self.assertEqual(again.key,'legacy-key-test')
        self.assertTrue(public['key_saved'])

    def test_unknown_key_reference_does_not_reuse_other_service_key(self):
        self.manager.configure({'api_key':'original-key-test','remember_key':True})
        with patch.dict(os.environ, {}, clear=True):
            public = self.manager.configure({'model_config':CATALOG,'active_profile':'GLM-5.3-Flash'})
        self.assertFalse(public['key_present'])
        self.assertEqual(self.manager.key,'')
        self.manager.configure({'active_profile':'DeepSeek-Flash'})
        self.assertEqual(self.manager.key,'original-key-test')

    def test_invalid_model_map_leaves_settings_and_key_unchanged(self):
        self.manager.configure({'api_key':'keep-key-test'})
        before = copy.deepcopy(self.manager.public_settings())
        broken = copy.deepcopy(CATALOG)
        broken['GLM-5.3-Flash']['settings']['max_tokens'] = -1
        for payload in ('{invalid', {}, broken, {'bad':{'api_key':'plaintext-secret'}},
                        {**CATALOG,'bad':{'provider_id':'x'}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.manager.configure({'model_config':payload})
            self.assertEqual(before,self.manager.public_settings())
            self.assertEqual(self.manager.key,'keep-key-test')

    def test_removing_settings_removes_cap_and_reasoning_override(self):
        self.manager.configure({'model_config':CATALOG,'active_profile':'DeepSeek-Flash','api_key':'test'})
        changed=copy.deepcopy(CATALOG)
        changed['DeepSeek-Flash']['settings']={}
        public=self.manager.configure({'model_config':changed,'active_profile':'DeepSeek-Flash'})
        self.assertIsNone(public['max_output_tokens'])
        generated=agent_config(self.root,public['model'],connection=public)
        self.assertNotIn('reasoningEffort',json.dumps(generated))
