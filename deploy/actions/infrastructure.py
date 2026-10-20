"""Generate a repository-scoped OIDC role; no compute resources or persistent AWS keys."""
import re

def template(bucket, subject):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise ValueError('Invalid bucket')
    if not re.fullmatch(r'repo:edwardxuuu-precious(?:@\d+)?/ashare-opening-volume-collector(?:@\d+)?:ref:refs/heads/main', subject):
        raise ValueError('Subject must identify the exact approved repository and main branch')
    arn = 'arn:aws:s3:::' + bucket
    return {
        'AWSTemplateFormatVersion':'2010-09-09',
        'Resources':{
            'GitHubProvider':{'Type':'AWS::IAM::OIDCProvider','Properties':{
                'Url':'https://token.actions.githubusercontent.com','ClientIdList':['sts.amazonaws.com']}},
            'CollectorRole':{'Type':'AWS::IAM::Role','Properties':{
                'MaxSessionDuration':21600,
                'AssumeRolePolicyDocument':{'Version':'2012-10-17','Statement':[{
                    'Effect':'Allow','Principal':{'Federated':{'Ref':'GitHubProvider'}},
                    'Action':'sts:AssumeRoleWithWebIdentity','Condition':{'StringEquals':{
                        'token.actions.githubusercontent.com:aud':'sts.amazonaws.com',
                        'token.actions.githubusercontent.com:sub':subject}}}]},
                'Policies':[{'PolicyName':'PrivateCollectorOnly','PolicyDocument':{
                    'Version':'2012-10-17','Statement':[
                        # S3 needs ListBucket to distinguish an absent optional state
                        # from an unauthorized object (404 versus 403).
                        {'Effect':'Allow','Action':['s3:ListBucket'],'Resource':arn},
                        {'Effect':'Allow','Action':['s3:GetObject','s3:PutObject'],
                         'Resource':[arn+'/data/*',arn+'/collector/checkpoint.tar.gz',
                                     arn+'/collector/actions-state.json',arn+'/collector/actions-lease.json',
                                     arn+'/collector/refresh-state.json',arn+'/collector/refresh-status.json',
                                     arn+'/collector/observations/*',arn+'/collector/chips/*']},
                        {'Effect':'Allow','Action':['s3:DeleteObject'],
                         'Resource':[arn+'/collector/actions-lease.json']}
                    ]}}], 'Tags':[{'Key':'Project','Value':'Stock'}]}}
        },
        'Outputs':{'RoleArn':{'Value':{'Fn::GetAtt':['CollectorRole','Arn']}}}
    }
