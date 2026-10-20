"""Admin-only account operations via the owner's existing AWS CLI identity.

No web signup or emails. Generated credentials are written to an owner-only file.
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
from credential_store import registry, set_password, store_path


def aws(service, operation, payload=None, profile='dev'):
    command = ['aws', service, operation, '--profile', profile, '--region', 'us-east-1',
               '--output', 'json', '--no-cli-pager']
    path = None
    try:
        if payload is not None:
            fd, path = tempfile.mkstemp(prefix='stock-account-', suffix='.json')
            with os.fdopen(fd, 'w') as handle:
                json.dump(payload, handle)
            command += ['--cli-input-json', 'file://' + path]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            # CLI errors do not include the request payload; never print tokens.
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout or '{}')
    finally:
        if path:
            os.unlink(path)


def config(profile):
    stack = aws('cloudformation', 'describe-stacks', {'StackName': 'stock-private-access'}, profile)
    return {item['OutputKey']: item['OutputValue'] for item in stack['Stacks'][0]['Outputs']}


def generate_password(length=None):
    if length is None:
        return secrets.token_urlsafe(24) + 'aA1!'
    if not 8 <= length <= 256:
        raise ValueError('密码长度必须为 8–256 位')
    groups = ('abcdefghjkmnpqrstuvwxyz', 'ABCDEFGHJKMNPQRSTUVWXYZ', '23456789', '!')
    characters = [secrets.choice(group) for group in groups]
    characters += [secrets.choice(''.join(groups[:3])) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(characters)
    return ''.join(characters)


def main():
    parser = argparse.ArgumentParser(description='创建、列出、停用或重置 Stock 账号')
    parser.add_argument('action', choices=['create', 'list', 'disable', 'enable', 'reset', 'vault'])
    parser.add_argument('username', nargs='?')
    parser.add_argument('--profile', default='dev')
    parser.add_argument('--password-length', type=int, help='指定随机密码长度，最少 8 位')
    args = parser.parse_args()
    if args.password_length is not None and not 8 <= args.password_length <= 256:
        parser.error('密码长度必须为 8–256 位')
    if args.action == 'vault':
        with registry():
            pass
        print(f'Credentials: {store_path()}')
        return
    settings = config(args.profile)
    pool = settings['UserPoolId']
    if args.action == 'list':
        users = aws('cognito-idp', 'list-users', {'UserPoolId': pool}, args.profile)['Users']
        print(json.dumps([{'username': user['Username'], 'enabled': user['Enabled'],
                           'status': user['UserStatus']} for user in users], ensure_ascii=False, indent=2))
        return
    name = args.username or ''
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', name):
        raise SystemExit('账号需为 3–64 位英文字母、数字、下划线、点或短横线')
    payload = {'UserPoolId': pool, 'Username': name}
    if args.action in ('disable', 'enable'):
        aws('cognito-idp', 'admin-' + args.action + '-user', payload, args.profile)
        print(f'{name}: {args.action} completed')
        return
    if args.action == 'create':
        aws('cognito-idp', 'admin-create-user', {**payload, 'MessageAction': 'SUPPRESS'}, args.profile)
    else:
        aws('cognito-idp', 'admin-user-global-sign-out', payload, args.profile)
    password = generate_password(args.password_length)
    set_password(name, password, lambda: aws('cognito-idp', 'admin-set-user-password',
        {**payload, 'Password': password, 'Permanent': True}, args.profile))
    print(f'Account ready. Credentials saved locally: {store_path()}')


if __name__ == '__main__':
    main()
