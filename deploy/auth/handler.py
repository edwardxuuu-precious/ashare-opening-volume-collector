"""Cognito sessions and private stock data. Never log requests or credentials."""
import base64
import gzip
import json
import os
import re
import time
from http.cookies import SimpleCookie

import boto3
from botocore.exceptions import ClientError

POOL = os.environ['USER_POOL_ID']
CLIENT = os.environ['USER_POOL_CLIENT_ID']
BUCKET = os.environ['DATA_BUCKET']
ORIGIN = os.environ['ALLOWED_ORIGIN']
ISSUER = f'https://cognito-idp.{os.environ["AWS_REGION"]}.amazonaws.com/{POOL}'
ACCESS = '__Host-stock-access'
REFRESH = '__Host-stock-refresh'
cognito = boto3.client('cognito-idp')
s3 = boto3.client('s3')
_history_store = None


def response(status, data, cookies=None):
    result = {'statusCode': status, 'headers': {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store, private',
        'x-content-type-options': 'nosniff',
        'access-control-allow-origin': ORIGIN,
        'access-control-allow-credentials': 'true',
        'vary': 'Origin',
    }, 'body': json.dumps(data, ensure_ascii=False)}
    if cookies:
        result['cookies'] = cookies
    return result


def accepts_gzip(header):
    """An explicit gzip q=0 overrides a wildcard; malformed qualities never opt in."""
    qualities = {}
    for part in header.lower().split(','):
        values = [value.strip() for value in part.split(';')]
        coding = values[0]
        if coding not in ('gzip', '*'):
            continue
        quality = 1.0
        for value in values[1:]:
            if value.startswith('q='):
                try:
                    quality = float(value[2:])
                except ValueError:
                    quality = 0.0
        qualities[coding] = quality if 0 <= quality <= 1 else 0.0
    return qualities.get('gzip', qualities.get('*', 0)) > 0


def data_response(data, accept_encoding):
    """Keep large stock-day data below Lambda's synchronous response envelope limit."""
    result = response(200, data)
    result['headers']['vary'] = 'Origin, Accept-Encoding'
    body = result['body'].encode('utf-8')
    if len(body) >= 65536 and accepts_gzip(accept_encoding):
        result['body'] = base64.b64encode(gzip.compress(body, compresslevel=6, mtime=0)).decode('ascii')
        result['headers']['content-encoding'] = 'gzip'
        result['isBase64Encoded'] = True
    # Includes JSON envelope + base64/escape expansion, with headroom below 6 MB.
    if len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > 5900000:
        return response(413, {'error': '行情数据响应过大，请使用支持 gzip 压缩传输的客户端。'})
    return result


def cookie(name, value='', age=0):
    return f'{name}={value}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={age}'


def cookies_from(event):
    jar = SimpleCookie()
    jar.load('; '.join(event.get('cookies', [])))
    return {key: item.value for key, item in jar.items()}


def current_user(token):
    # GetUser validates the signature, expiry and revocation at Cognito. Check
    # the pool AND client first so a token issued to another app cannot enter.
    try:
        segment = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
        if not isinstance(claims, dict):
            return None
        if (claims.get('iss') != ISSUER or claims.get('client_id') != CLIENT
                or claims.get('token_use') != 'access'
                or claims.get('exp', 0) <= time.time()):
            return None
        return cognito.get_user(AccessToken=token)['Username']
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    except ClientError as exc:
        if exc.response['Error']['Code'] in ('NotAuthorizedException', 'UserNotFoundException'):
            return None
        raise


def is_chip_data_path(path):
    # Exact private-data additions only. Existing Cognito validation runs first.
    return bool(re.fullmatch(r'/data/chips/(?:manifest\.json|[a-f0-9]{16}/(?:screening|\d{6})\.json|daily/(?:manifest\.json|status\.json|[a-f0-9]{16}/(?:screening|\d{6})\.json))', path))


def handler(event, context):
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    method = event.get('requestContext', {}).get('http', {}).get('method', '')
    path = event.get('rawPath', '')
    try:
        if method == 'OPTIONS':
            if headers.get('origin') != ORIGIN:
                return response(403, {'error': '来源不允许'})
            result = response(204, {})
            result['headers'].update({'access-control-allow-methods': 'GET, POST, OPTIONS',
                                      'access-control-allow-headers': 'Content-Type'})
            return result
        # Cookie-bearing writes require the exact site origin, not just CORS.
        if method == 'POST' and headers.get('origin') != ORIGIN:
            return response(403, {'error': '来源不允许'})
        jar = cookies_from(event)
        if method == 'POST' and path == '/auth/login':
            body = event.get('body', '')
            if event.get('isBase64Encoded'):
                body = base64.b64decode(body).decode()
            if len(body) > 4096:
                return response(400, {'error': '登录信息无效'})
            payload = json.loads(body)
            if not isinstance(payload, dict):
                return response(400, {'error': '请求格式无效'})
            username = payload.get('username', '')
            password = payload.get('password', '')
            if (not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', username)
                    or not isinstance(password, str) or not 1 <= len(password) <= 256):
                return response(401, {'error': '账号或密码不正确，或账号已停用'})
            try:
                result = cognito.admin_initiate_auth(UserPoolId=POOL, ClientId=CLIENT,
                    AuthFlow='ADMIN_USER_PASSWORD_AUTH',
                    AuthParameters={'USERNAME': username, 'PASSWORD': password})
            except ClientError as exc:
                if exc.response['Error']['Code'] in ('TooManyRequestsException', 'LimitExceededException'):
                    return response(429, {'error': '尝试次数过多，请稍后再试'})
                if exc.response['Error']['Code'] in ('NotAuthorizedException', 'UserNotFoundException',
                    'PasswordResetRequiredException', 'UserNotConfirmedException'):
                    return response(401, {'error': '账号或密码不正确，或账号已停用'})
                raise
            tokens = result.get('AuthenticationResult')
            if not tokens:
                return response(401, {'error': '请联系管理员完成账号设置'})
            return response(200, {'username': username}, [
                cookie(ACCESS, tokens['AccessToken'], tokens['ExpiresIn']),
                cookie(REFRESH, tokens['RefreshToken'], 86400),
            ])
        if method == 'POST' and path == '/auth/logout':
            if jar.get(REFRESH):
                try:
                    cognito.revoke_token(Token=jar[REFRESH], ClientId=CLIENT)
                except ClientError as exc:
                    if exc.response['Error']['Code'] not in ('NotAuthorizedException', 'InvalidParameterException'):
                        raise
            return response(200, {'ok': True}, [cookie(ACCESS), cookie(REFRESH)])
        user = current_user(jar.get(ACCESS, ''))
        if not user:
            return response(401, {'error': '请先登录'}, [cookie(ACCESS)])
        if method == 'GET' and path == '/auth/me':
            return response(200, {'username': user})
        historical = re.fullmatch(r'/data/chips/daily/([a-f0-9]{16})/(\d{6})/history/(\d{4}-\d{2}-\d{2})\.json', path)
        if method == 'GET' and historical:
            from backend.chips.history import load_history, load_history_batch, ImmutableHistoryStore
            global _history_store
            if _history_store is None:
                _history_store = ImmutableHistoryStore(s3)
            try:
                query = event.get('queryStringParameters') or {}
                if query and query != {'batch':'8'}:
                    return response(400, {'error':'历史读取参数无效'})
                load = load_history_batch if query else load_history
                value = load(_history_store, BUCKET, *historical.groups())
                return data_response(value, headers.get('accept-encoding', ''))
            except ValueError as exc:
                return response(422, {'error': '该日期筹码暂无法核实，请选择其他日期。', 'reason': str(exc)[:180]})
            except ClientError as exc:
                if exc.response['Error']['Code'] == 'NoSuchKey':
                    return response(404, {'error': '该股票历史行情尚未发布'})
                raise
        if method == 'GET' and is_chip_data_path(path):
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=path.lstrip('/'))
                raw = obj['Body'].read()
                if raw[:2] == b'\x1f\x8b':
                    raw = gzip.decompress(raw)
                return data_response(json.loads(raw), headers.get('accept-encoding', ''))
            except ClientError as exc:
                if exc.response['Error']['Code'] == 'NoSuchKey':
                    return response(404, {'error': '筹码数据尚未发布'})
                raise
        if method == 'GET' and re.fullmatch(r'/data/(manifest|stocks|collection-status|\d{4}-\d{2}-\d{2})\.json', path):
            try:
                if re.fullmatch(r'/data/\d{4}-\d{2}-\d{2}\.json', path):
                    # GetObject without ListBucket conceals missing keys as
                    # AccessDenied. The committed catalog is the publication
                    # authority; keep a real saved-object failure as 503.
                    catalog = s3.get_object(Bucket=BUCKET, Key='data/manifest.json')
                    try:
                        manifest = json.loads(catalog['Body'].read())
                    except ValueError:
                        return response(503, {'error': '服务暂时不可用，请稍后重试'})
                    if not isinstance(manifest, dict) or not isinstance(manifest.get('dates'), list):
                        return response(503, {'error': '服务暂时不可用，请稍后重试'})
                    filename = path.rsplit('/', 1)[1]
                    if not any(isinstance(entry, dict) and entry.get('date') == filename[:-5]
                               and entry.get('file') == filename for entry in manifest['dates']):
                        return response(404, {'error': '数据尚未保存'})
                obj = s3.get_object(Bucket=BUCKET, Key=path.lstrip('/'))
                return data_response(json.loads(obj['Body'].read()), headers.get('accept-encoding', ''))
            except ClientError as exc:
                if exc.response['Error']['Code'] == 'NoSuchKey':
                    return response(404, {'error': '数据尚未保存'})
                raise
        return response(404, {'error': '接口不存在'})
    except (ValueError, TypeError, UnicodeError):
        return response(400, {'error': '请求格式无效'})
    except ClientError:
        return response(503, {'error': '服务暂时不可用，请稍后重试'})
