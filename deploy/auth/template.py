"""Generate the reviewable CloudFormation template; does not deploy anything."""
import json
from pathlib import Path
from access_logging import add_access_logs
from chip_bundle import inline_handler

ref = lambda name: {'Ref': name}
get = lambda name, attr: {'Fn::GetAtt': [name, attr]}
sub = lambda text: {'Fn::Sub': text}
resources = {
    'UserPool': {'Type': 'AWS::Cognito::UserPool', 'DeletionPolicy': 'Retain', 'Properties': {
        'UserPoolName': 'stock-iteready-users',
        'AdminCreateUserConfig': {'AllowAdminCreateUserOnly': True},
        'UsernameConfiguration': {'CaseSensitive': False},
        'AccountRecoverySetting': {'RecoveryMechanisms': [{'Name': 'admin_only', 'Priority': 1}]},
        'Policies': {'PasswordPolicy': {'MinimumLength': 8, 'RequireLowercase': True,
            'RequireUppercase': True, 'RequireNumbers': True, 'RequireSymbols': True}},
        'MfaConfiguration': 'OFF',
    }},
    'UserClient': {'Type': 'AWS::Cognito::UserPoolClient', 'Properties': {
        'ClientName': 'stock-server-sessions', 'UserPoolId': ref('UserPool'), 'GenerateSecret': False,
        'ExplicitAuthFlows': ['ALLOW_ADMIN_USER_PASSWORD_AUTH'],
        'PreventUserExistenceErrors': 'ENABLED', 'EnableTokenRevocation': True,
        'AccessTokenValidity': 1, 'IdTokenValidity': 1, 'RefreshTokenValidity': 1,
        'TokenValidityUnits': {'AccessToken': 'hours', 'IdToken': 'hours', 'RefreshToken': 'days'},
    }},
    'DataBucket': {'Type': 'AWS::S3::Bucket', 'DeletionPolicy': 'Retain', 'Properties': {
        'PublicAccessBlockConfiguration': {'BlockPublicAcls': True, 'BlockPublicPolicy': True,
            'IgnorePublicAcls': True, 'RestrictPublicBuckets': True},
        'OwnershipControls': {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]},
        'BucketEncryption': {'ServerSideEncryptionConfiguration': [
            {'ServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]},
        'VersioningConfiguration': {'Status': 'Enabled'},
        'LifecycleConfiguration': {'Rules': [
            {'Id': 'stock-data-old-versions-seven-days', 'Status': 'Enabled', 'Prefix': 'data/',
             'NoncurrentVersionExpiration': {'NoncurrentDays': 7},
             'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}},
            {'Id': 'stock-backup-old-versions-seven-days', 'Status': 'Enabled', 'Prefix': 'collector/checkpoint.tar.gz',
             'NoncurrentVersionExpiration': {'NoncurrentDays': 7}},
            {'Id': 'stock-imports-seven-days', 'Status': 'Enabled', 'Prefix': 'collector/imports/',
             'ExpirationInDays': 7, 'NoncurrentVersionExpiration': {'NoncurrentDays': 7}},
        ]},
    }},
    'DataPolicy': {'Type': 'AWS::S3::BucketPolicy', 'Properties': {
        'Bucket': ref('DataBucket'), 'PolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
            'Resource': [get('DataBucket', 'Arn'), sub('${DataBucket.Arn}/*')],
            'Condition': {'Bool': {'aws:SecureTransport': 'false'}},
        }]}},
    },
    'Role': {'Type': 'AWS::IAM::Role', 'Properties': {
        'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]},
        'Policies': [{'PolicyName': 'StockSessionsAndDataRead', 'PolicyDocument': {
            'Version': '2012-10-17', 'Statement': [
                {'Effect': 'Allow', 'Action': ['cognito-idp:AdminInitiateAuth'], 'Resource': get('UserPool', 'Arn')},
                {'Effect': 'Allow', 'Action': ['s3:GetObject'], 'Resource': sub('${DataBucket.Arn}/data/*')},
                {'Effect': 'Allow', 'Action': ['s3:GetObject'], 'Resource': sub('${DataBucket.Arn}/collector/chips/raw/*')},
                {'Effect': 'Allow', 'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'],
                 'Resource': sub('arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/stock-auth-api:*')},
            ]}}],
    }},
    'Logs': {'Type': 'AWS::Logs::LogGroup', 'Properties': {
        'LogGroupName': '/aws/lambda/stock-auth-api', 'RetentionInDays': 14}},
    'Function': {'Type': 'AWS::Lambda::Function', 'DependsOn': 'Logs', 'Properties': {
        'FunctionName': 'stock-auth-api', 'Runtime': 'python3.12', 'Handler': 'index.handler',
        'Role': get('Role', 'Arn'), 'Timeout': 15, 'MemorySize': 512,
        'Environment': {'Variables': {'USER_POOL_ID': ref('UserPool'),
            'USER_POOL_CLIENT_ID': ref('UserClient'), 'DATA_BUCKET': ref('DataBucket'),
            'ALLOWED_ORIGIN': 'https://stock.iteready.com'}},
        'Code': {'ZipFile': inline_handler()},
    }},
    'Api': {'Type': 'AWS::ApiGatewayV2::Api', 'Properties': {
        'Name': 'stock-auth-api', 'ProtocolType': 'HTTP', 'DisableExecuteApiEndpoint': True}},
    'Integration': {'Type': 'AWS::ApiGatewayV2::Integration', 'Properties': {
        'ApiId': ref('Api'), 'IntegrationType': 'AWS_PROXY',
        'IntegrationUri': get('Function', 'Arn'), 'PayloadFormatVersion': '2.0'}},
    'DefaultRoute': {'Type': 'AWS::ApiGatewayV2::Route', 'Properties': {
        'ApiId': ref('Api'), 'RouteKey': '$default', 'Target': sub('integrations/${Integration}')}},
    'LoginRoute': {'Type': 'AWS::ApiGatewayV2::Route', 'Properties': {
        'ApiId': ref('Api'), 'RouteKey': 'POST /auth/login', 'Target': sub('integrations/${Integration}')}},
    'Stage': {'Type': 'AWS::ApiGatewayV2::Stage', 'DependsOn': ['DefaultRoute', 'LoginRoute'], 'Properties': {
        'ApiId': ref('Api'), 'StageName': '$default', 'AutoDeploy': True,
        'DefaultRouteSettings': {'ThrottlingBurstLimit': 40, 'ThrottlingRateLimit': 20},
        'RouteSettings': {'POST /auth/login': {'ThrottlingBurstLimit': 5, 'ThrottlingRateLimit': 1}},
    }},
    'InvokePermission': {'Type': 'AWS::Lambda::Permission', 'Properties': {
        'Action': 'lambda:InvokeFunction', 'FunctionName': ref('Function'),
        'Principal': 'apigateway.amazonaws.com', 'SourceArn': sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/*')}},
    'Certificate': {'Type': 'AWS::CertificateManager::Certificate', 'Properties': {
        'DomainName': 'api.stock.iteready.com', 'ValidationMethod': 'DNS',
        'DomainValidationOptions': [{'DomainName': 'api.stock.iteready.com', 'HostedZoneId': 'Z0072410BLK5SPP65UQW'}]}},
    'Domain': {'Type': 'AWS::ApiGatewayV2::DomainName', 'Properties': {
        'DomainName': 'api.stock.iteready.com', 'DomainNameConfigurations': [{
            'CertificateArn': ref('Certificate'), 'EndpointType': 'REGIONAL', 'SecurityPolicy': 'TLS_1_2'}]}},
    'Mapping': {'Type': 'AWS::ApiGatewayV2::ApiMapping', 'Properties': {
        'ApiId': ref('Api'), 'DomainName': ref('Domain'), 'Stage': ref('Stage')}},
    'Dns': {'Type': 'AWS::Route53::RecordSet', 'Properties': {
        'HostedZoneId': 'Z0072410BLK5SPP65UQW', 'Name': 'api.stock.iteready.com.', 'Type': 'A',
        'AliasTarget': {'DNSName': get('Domain', 'RegionalDomainName'), 'HostedZoneId': get('Domain', 'RegionalHostedZoneId')}}},
}
template = {'AWSTemplateFormatVersion': '2010-09-09', 'Description': 'Private Stock accounts and read-only market data',
    'Resources': resources, 'Outputs': {
        'UserPoolId': {'Value': ref('UserPool')}, 'UserPoolClientId': {'Value': ref('UserClient')},
        'DataBucket': {'Value': ref('DataBucket')}, 'ApiUrl': {'Value': 'https://api.stock.iteready.com'},
        'ApiId': {'Value': ref('Api')},
    }}
if __name__ == '__main__':
    template = add_access_logs(template)
    destination = Path(__file__).with_name('template.json')
    destination.write_text(json.dumps(template, ensure_ascii=False, indent=2))
    print(destination)
