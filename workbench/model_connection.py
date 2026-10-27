"""Provider connection data only; importing JSON never changes agent permissions."""
import json
import re
from urllib.parse import urlsplit

MODEL_MAX_OUTPUT_TOKENS = 393216
ADAPTERS = {'deepseek': '@ai-sdk/deepseek', 'openai-compatible': '@ai-sdk/openai-compatible',
            'openai-responses': '@ai-sdk/openai', 'anthropic': '@ai-sdk/anthropic'}
DEFAULT_CONNECTION = {'provider_id': 'deepseek', 'provider_name': 'DeepSeek', 'api_protocol': 'deepseek',
                      'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash',
                      'api_headers': {}, 'context_tokens': 1048576, 'model_output_tokens': MODEL_MAX_OUTPUT_TOKENS,
                      'api_key_env':'DEEPSEEK_API_KEY', 'usage_adapter':'deepseek-v1', 'model_settings':{}}
CONNECTION_FIELDS = frozenset(DEFAULT_CONNECTION)


def credential_target(settings):
    return tuple(settings.get(k, DEFAULT_CONNECTION[k]) for k in ('provider_id', 'api_protocol', 'base_url'))


def normalize_connection(current=None, updates=None):
    old = {**DEFAULT_CONNECTION, **(current or {})}
    values = {k: old[k] for k in CONNECTION_FIELDS}
    values.update({k:v for k,v in (updates or {}).items() if k in CONNECTION_FIELDS})
    for key in ('provider_id', 'provider_name', 'api_protocol', 'base_url', 'model'):
        value = values[key]
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError(key + ' 必须为非空文本')
        if re.search(r'\{(?:env|file):', value, re.I):
            raise ValueError('连接字段应填写实际值，不支持文件或环境变量插值；密钥使用专用输入框')
        values[key] = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', values['provider_id']):
        raise ValueError('服务标识只能包含字母、数字、点、横线和下划线')
    if values['api_protocol'] not in ADAPTERS:
        raise ValueError('请选择支持的 API 协议')
    if re.search(r'\s', values['model']):
        raise ValueError('模型标识不能包含空格')
    url = urlsplit(values['base_url'])
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or url.query or url.fragment or re.search(r'\s|[{}]', values['base_url'])):
        raise ValueError('Base URL 应为 http/https API 地址，不能包含用户名、密码、查询参数或占位符')
    try:
        url.port
    except ValueError:
        raise ValueError('Base URL 端口无效') from None
    values['base_url'] = values['base_url'].rstrip('/')
    changed = credential_target(values) != credential_target(old) or values['model'] != old['model']
    if changed:
        official = values['api_protocol'] == 'deepseek' and url.hostname == 'api.deepseek.com'
        for key in ('context_tokens', 'model_output_tokens'):
            if key not in (updates or {}):
                values[key] = DEFAULT_CONNECTION[key] if official else None
    for key in ('context_tokens', 'model_output_tokens'):
        if values[key] is not None and (type(values[key]) is not int or values[key] < 1):
            raise ValueError(key + ' 应留空或填写模型实际支持的正整数')
    if (values['context_tokens'] is None) != (values['model_output_tokens'] is None):
        raise ValueError('OpenCode 的模型上下文容量和最大输出容量需要同时填写，或都留空')
    if not isinstance(values['api_key_env'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', values['api_key_env']):
        raise ValueError('api_key_env 应为环境变量名称，例如 MICUAPI_API_KEY')
    if values['usage_adapter'] not in ('openai-chat-v1','deepseek-v1','openai-responses-v1','anthropic-v1'):
        raise ValueError('usage_adapter 支持 openai-chat-v1、deepseek-v1、openai-responses-v1、anthropic-v1')
    settings = values['model_settings']
    if not isinstance(settings, dict):
        raise ValueError('settings 必须为 JSON 对象')
    try:
        encoded = json.dumps(settings, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('settings 必须为有效 JSON，不能含 NaN/Infinity') from None
    if re.search(r'\{(?:env|file):|"(?:api_key|apiKey|Authorization|Cookie)"\s*:', encoded, re.I):
        raise ValueError('settings 不接收密钥或文件/环境变量插值，请使用 api_key_env 引用密钥')
    cap = output_cap(settings)
    if cap is not None and (type(cap) is not int or cap < 1):
        raise ValueError('settings.max_tokens 应为正整数，或删除该字段恢复默认')
    headers = values['api_headers']
    if not isinstance(headers, dict):
        raise ValueError('附加请求头必须为 JSON 对象')
    for key, value in headers.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                or not isinstance(value, str) or any(c in value for c in '\r\n\0')):
            raise ValueError('请求头名称或值无效')
        if re.search(r'authorization|cookie|api.?key|token|secret', key, re.I):
            raise ValueError('鉴权请求头请改用专用 API Key 输入框，附加请求头不会加密保存')
        if re.search(r'\{(?:env|file):', value, re.I):
            raise ValueError('请求头应填写实际值，不支持文件或环境变量插值')
    values['api_headers'] = {key.title():value for key,value in headers.items()}
    return values


def output_cap(settings):
    fields = [settings[k] for k in ('max_tokens','max_output_tokens','max_completion_tokens') if k in settings]
    if len(fields) > 1 and any(v != fields[0] for v in fields[1:]):
        raise ValueError('settings 中多个输出上限互相冲突，请保留一种写法')
    return fields[0] if fields else None


def api_base(settings):
    base = settings['base_url'].rstrip('/')
    # Claude CLI accepts a host URL; the Anthropic SDK expects the /v1 base.
    if settings['api_protocol'] == 'anthropic' and not urlsplit(base).path:
        base += '/v1'
    return base


def provider_definition(settings, max_output_tokens=None):
    connection = normalize_connection(settings)
    options = {'baseURL': api_base(connection), 'apiKey': '{env:' + connection['api_key_env'] + '}'}
    if connection['api_headers']:
        options['headers'] = connection['api_headers']
    model = {'name': connection['model'], 'tool_call': True}
    if 'temperature' in connection['model_settings']:
        model['temperature'] = True
    sdk_settings = {k:v for k,v in connection['model_settings'].items()
                    if k not in ('max_tokens','max_output_tokens','max_completion_tokens','temperature','top_p','topP')}
    if 'reasoning_effort' in sdk_settings and 'reasoningEffort' not in sdk_settings:
        sdk_settings['reasoningEffort'] = sdk_settings.pop('reasoning_effort')
    if sdk_settings:
        model['options'] = sdk_settings
    if connection['api_headers']:
        # OpenCode sets request-level User-Agent after SDK provider options.
        # Model headers take precedence in its request preparation step.
        model['headers'] = connection['api_headers']
    limits = {k:v for k,v in {'context': connection['context_tokens'],
                             'output': max_output_tokens or output_cap(connection['model_settings']) or connection['model_output_tokens']}.items() if v is not None}
    # OpenCode requires both keys when a model limit object is present.
    # A user output cap alone is applied via the runtime's request override.
    if 'context' in limits and 'output' in limits:
        model['limit'] = limits
    return {'npm': ADAPTERS[connection['api_protocol']], 'name': connection['provider_name'],
            'options': options, 'models': {connection['model']: model}}


def export_connection(settings):
    provider = settings['provider_id']
    return {'model': provider + '/' + settings['model'], 'provider': {provider: provider_definition(settings)}}


def parse_connection_json(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as error:
        raise ValueError(f'JSON 第 {error.lineno} 行格式错误：{error.msg}') from None
    if not isinstance(data, dict):
        raise ValueError('JSON 配置必须为对象')
    if 'provider' not in data:
        if not {'base_url', 'model'} <= set(data):
            raise ValueError('需要 base_url/model，或 OpenCode 的 provider/model 配置')
        extra = set(data) - CONNECTION_FIELDS - {'api_key'}
        if extra:
            raise ValueError('连接配置不支持字段：' + ', '.join(sorted(extra)))
        values = {'provider_id':'custom', 'provider_name':'自定义服务', 'api_protocol':'openai-compatible', **data}
    else:
        providers = data['provider']
        if not isinstance(providers, dict) or not providers:
            raise ValueError('provider 必须包含服务配置')
        selected = data.get('model', '')
        if not isinstance(selected, str):
            raise ValueError('model 必须为 服务标识/模型标识')
        provider, _, model = selected.partition('/')
        if not provider and len(providers) == 1:
            provider = next(iter(providers))
        if provider not in providers:
            raise ValueError('请用 model: "服务标识/模型标识" 明确选择服务和模型')
        config = providers[provider]
        if not isinstance(config, dict):
            raise ValueError('服务配置必须为对象')
        models = config.get('models', {})
        if not model and isinstance(models, dict) and len(models) == 1:
            model = next(iter(models))
        if not model or not isinstance(models, dict):
            raise ValueError('请在 model 中选择一个模型')
        options = config.get('options', {})
        details = models.get(model, {})
        if not isinstance(options, dict) or not isinstance(details, dict):
            raise ValueError('options/models 格式错误')
        npm = config.get('npm', {'deepseek':'@ai-sdk/deepseek', 'openai':'@ai-sdk/openai',
                                 'anthropic':'@ai-sdk/anthropic'}.get(provider, '@ai-sdk/openai-compatible'))
        reverse = {v:k for k,v in ADAPTERS.items()}
        if npm not in reverse:
            raise ValueError('该 SDK 暂未接入：请选择 DeepSeek、Chat Completions、Responses 或 Anthropic')
        limits = details.get('limit', {})
        if not isinstance(limits, dict):
            raise ValueError('模型 limit 必须为对象')
        provider_headers, model_headers = options.get('headers', {}), details.get('headers', {})
        if not isinstance(provider_headers, dict) or not isinstance(model_headers, dict):
            raise ValueError('附加请求头必须为 JSON 对象')
        headers = {str(k).title():v for k,v in provider_headers.items()}
        headers.update({str(k).title():v for k,v in model_headers.items()})
        values = {'provider_id':provider, 'provider_name':config.get('name', provider), 'api_protocol':reverse[npm],
                  'base_url':options.get('baseURL', ''), 'model':model, 'api_headers':headers,
                  'context_tokens':limits.get('context'), 'model_output_tokens':limits.get('output')}
        if 'apiKey' in options:
            values['api_key'] = options['apiKey']
    key = values.pop('api_key', None)
    connection = normalize_connection(updates=values)
    if key is not None:
        if not isinstance(key, str):
            raise ValueError('api_key 必须为文本')
        if key and not key.startswith('{env:'):
            connection['api_key'] = key
    return connection
