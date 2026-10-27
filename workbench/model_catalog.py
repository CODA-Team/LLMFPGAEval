"""User-facing alias -> model JSON, translated to a selected OpenCode provider."""
import copy
import json
from .model_connection import ADAPTERS, normalize_connection, output_cap

PROFILE_FIELDS = {'provider_id','model_id','base_url','api_key_env','npm','usage_adapter','user_agent','settings','headers','limit'}


def parse_catalog(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f'JSON 第 {error.lineno} 行：{error.msg}') from None
    if not isinstance(data, dict) or not data:
        raise ValueError('请填写“模型名称 → 配置对象”的 JSON，至少包含一个模型')
    for alias, entry in data.items():
        if not isinstance(alias, str) or not alias.strip() or any(ord(c)<32 for c in alias):
            raise ValueError('模型名称必须是非空文本')
        if not isinstance(entry, dict):
            raise ValueError(alias + ' 的配置必须为 JSON 对象')
        extra = set(entry) - PROFILE_FIELDS
        if extra:
            raise ValueError(alias + ' 不支持字段：' + ', '.join(sorted(extra)) + '；密钥请用 api_key_env 引用')
        missing = {'provider_id','model_id','base_url','api_key_env','npm'} - set(entry)
        if missing:
            raise ValueError(alias + ' 缺少字段：' + ', '.join(sorted(missing)))
        profile_connection(alias, entry)
    return data


def profile_connection(alias, entry):
    reverse = {v:k for k,v in ADAPTERS.items()}
    if entry.get('npm') not in reverse:
        raise ValueError(alias + ' 的 npm 尚未接入；支持 @ai-sdk/openai-compatible、@ai-sdk/openai、@ai-sdk/deepseek、@ai-sdk/anthropic')
    protocol = reverse[entry['npm']]
    headers = entry.get('headers', {})
    if not isinstance(headers, dict):
        raise ValueError(alias + '.headers 必须为 JSON 对象')
    headers = dict(headers)
    if 'user_agent' in entry:
        headers = {k:v for k,v in headers.items() if k.lower() != 'user-agent'}
        headers['User-Agent'] = entry['user_agent']
    limits = entry.get('limit', {})
    if not isinstance(limits, dict) or set(limits)-{'context','output'}:
        raise ValueError(alias + '.limit 只接受 context 和 output')
    usage = entry.get('usage_adapter', {'deepseek':'deepseek-v1','openai-compatible':'openai-chat-v1',
                                      'openai-responses':'openai-responses-v1','anthropic':'anthropic-v1'}[protocol])
    return normalize_connection(updates={'provider_id':entry['provider_id'],'provider_name':alias,'api_protocol':protocol,
        'model':entry['model_id'],'base_url':entry['base_url'],'api_key_env':entry['api_key_env'],
        'usage_adapter':usage,'model_settings':entry.get('settings',{}),'api_headers':headers,
        'context_tokens':limits.get('context'),'model_output_tokens':limits.get('output')})


def connection_profile(connection):
    """Convert the previous single-connection configuration without losing limits."""
    value = normalize_connection(connection)
    settings = copy.deepcopy(value['model_settings'])
    if connection.get('max_output_tokens') is not None:
        settings['max_tokens'] = connection['max_output_tokens']
    entry = {'provider_id':value['provider_id'],'model_id':value['model'],'base_url':value['base_url'],
             'api_key_env':value['api_key_env'],'npm':ADAPTERS[value['api_protocol']],
             'usage_adapter':value['usage_adapter'],'settings':settings}
    headers = dict(value['api_headers'])
    if 'User-Agent' in headers:
        entry['user_agent'] = headers.pop('User-Agent')
    if headers:
        entry['headers'] = headers
    if value['context_tokens'] is not None:
        entry['limit'] = {'context':value['context_tokens'],'output':value['model_output_tokens']}
    return entry
