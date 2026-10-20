"""Exercise a disposable account. Never print passwords, cookies or tokens."""
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import urllib.error
import urllib.request

from accounts import aws, config
from credential_store import load_credentials

ROOT = Path(__file__).resolve().parents[2]
settings = config('dev')
credentials = load_credentials('stock-auth-check')
assert credentials['username'] == 'stock-auth-check'
jar = CookieJar()
browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
results = []


def call(name, path, expected, body=None, cookie=None, origin='https://stock.iteready.com'):
    headers = {'Origin': origin}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if cookie:
        headers['Cookie'] = cookie
    request = urllib.request.Request(settings['ApiUrl'] + path, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    try:
        response = (urllib.request.urlopen if cookie else browser.open)(request, timeout=25)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        result = {'name': name, 'http': response.status, 'expected': expected,
                  'passed': response.status == expected}
        results.append(result)
        print(json.dumps(result), flush=True)
        if response.status != expected:
            raise SystemExit('Live auth check failed: ' + name)
        return raw, response.headers


try:
    call('anonymous data denied', '/data/manifest.json', 401)
    call('forged cookie denied', '/data/manifest.json', 401, cookie='__Host-stock-access=forged')
    call('cross-origin login denied', '/auth/login', 403, body={}, origin='https://example.org')
    call('incorrect password denied', '/auth/login', 401,
         body={'username': credentials['username'], 'password': 'incorrect-password'})
    raw, headers = call('issued account login', '/auth/login', 200, body=credentials)
    assert b'AccessToken' not in raw and b'RefreshToken' not in raw
    assert all('HttpOnly' in value and 'Secure' in value and 'SameSite=Strict' in value
               for value in headers.get_all('Set-Cookie'))
    old_cookie = '; '.join(cookie.name + '=' + cookie.value for cookie in jar)
    call('signed-in identity', '/auth/me', 200)
    data, _ = call('private data after login', '/data/manifest.json', 200)
    source = Path(json.loads((ROOT / 'outputs/auth-build-input.json').read_text())['dataSource'])
    assert json.loads(data) == json.loads((source / 'manifest.json').read_text())
    call('logout', '/auth/logout', 200, body={})
    call('logged-out data denied', '/data/manifest.json', 401)
    call('replayed logged-out session denied', '/data/manifest.json', 401, cookie=old_cookie)
    call('new session login', '/auth/login', 200, body=credentials)
    disabled_cookie = '; '.join(cookie.name + '=' + cookie.value for cookie in jar)
    aws('cognito-idp', 'admin-disable-user', {'UserPoolId': settings['UserPoolId'],
                                           'Username': credentials['username']})
    call('disabled user session denied', '/data/manifest.json', 401, cookie=disabled_cookie)
    call('disabled user password denied', '/auth/login', 401, body=credentials)
    pool = aws('cognito-idp', 'describe-user-pool', {'UserPoolId': settings['UserPoolId']})['UserPool']
    assert pool['AdminCreateUserConfig']['AllowAdminCreateUserOnly'] is True
    results.append({'name': 'self-registration disabled', 'passed': True})
finally:
    (ROOT / 'outputs/auth-live-verification.json').write_text(json.dumps(results, indent=2))
