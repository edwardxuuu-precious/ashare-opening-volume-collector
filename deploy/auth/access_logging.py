"""Add HTTP access logs without overwriting existing stack resources or code drift."""
import copy
import json

LOG_GROUP = '/aws/apigateway/stock-auth-api-access'
LOG_FIELDS = {
    'requestId': '$context.requestId',
    'timeEpoch': '$context.requestTimeEpoch',
    'method': '$context.httpMethod',
    'path': '$context.path',
    'status': '$context.status',
    'responseLatencyMs': '$context.responseLatency',
    'integrationStatus': '$context.integrationStatus',
    'integrationResponseStatus': '$context.integration.status',
    'integrationRequestId': '$context.integration.requestId',
    'integrationError': '$context.integrationErrorMessage',
    'errorType': '$context.error.responseType',
}


def add_access_logs(template):
    """Patch the freshly read production template, preserving unrelated drift."""
    result = copy.deepcopy(template)
    resources = result['Resources']
    stage = resources['Stage']
    if (stage['Type'] != 'AWS::ApiGatewayV2::Stage' or
            stage['Properties'].get('StageName') != '$default' or
            stage['Properties'].get('ApiId') != {'Ref': 'Api'}):
        raise ValueError('Unexpected API stage identity')
    log_resource = {'Type': 'AWS::Logs::LogGroup', 'Properties': {
        'LogGroupName': LOG_GROUP, 'RetentionInDays': 14}}
    if 'AccessLogs' in resources and resources['AccessLogs'] != log_resource:
        raise ValueError('Existing access log resource differs; review first')
    resources['AccessLogs'] = log_resource
    settings = {
        'DestinationArn': {'Fn::GetAtt': ['AccessLogs', 'Arn']},
        'Format': json.dumps(LOG_FIELDS, separators=(',', ':')),
    }
    previous = stage['Properties'].get('AccessLogSettings')
    if previous is not None and previous != settings:
        raise ValueError('Existing API access log settings differ; review first')
    stage['Properties']['AccessLogSettings'] = settings
    return result


def assert_log_only_change(before, after):
    expected = add_access_logs(before)
    if after != expected:
        raise ValueError('Change includes resources beyond access logs')
