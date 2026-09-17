"""Generate a disabled-by-default low-cost Scheduler/Lambda CloudFormation stack."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CRYPTOGRAPHY_VERSION = "50.0.1"


def template(bucket, code_key, private_key_parameter, app_id, installation_id, repository_id, enabled=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Invalid bucket")
    if not re.fullmatch(r"collector/dispatcher-code/[A-Za-z0-9._/-]+\.zip", code_key) or ".." in code_key:
        raise ValueError("Code must use a scoped dispatcher artifact key")
    if not re.fullmatch(r"/stock/scheduler/[A-Za-z0-9_/-]+", private_key_parameter) or ".." in private_key_parameter:
        raise ValueError("Private key must be an exact /stock/scheduler/ SSM parameter")
    for value in (app_id, installation_id, repository_id):
        if not re.fullmatch(r"[1-9][0-9]*", str(value)):
            raise ValueError("App, installation and repository IDs must be positive integers")
    bucket_arn = "arn:aws:s3:::" + bucket
    sub = lambda text: {"Fn::Sub": text}
    resources = {
        "ScheduleGroup": {"Type": "AWS::Scheduler::ScheduleGroup"},
        "LogGroup": {"Type": "AWS::Logs::LogGroup", "Properties": {
            "LogGroupName": sub("/aws/lambda/${AWS::StackName}-dispatcher"), "RetentionInDays": 7}},
        "DispatcherRole": {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
            "Policies": [{"PolicyName": "DispatchOnly", "PolicyDocument": {
                "Version": "2012-10-17", "Statement": [
                    {"Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter" + private_key_parameter)},
                    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": bucket_arn,
                     "Condition": {"StringEquals": {"s3:prefix": ["collector/refresh-status.json", "data/collection-status.json", "collector/actions-lease.json", "collector/dispatcher-state.json"]}}},
                    {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [bucket_arn + "/" + key for key in (
                        "collector/refresh-status.json", "data/collection-status.json", "collector/actions-lease.json", "collector/dispatcher-state.json")]},
                    {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": bucket_arn + "/collector/dispatcher-state.json"},
                    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": {"Fn::GetAtt": ["LogGroup", "Arn"]}}
                ]}}]}},
        "Dispatcher": {"Type": "AWS::Lambda::Function", "Properties": {
            "FunctionName": sub("${AWS::StackName}-dispatcher"), "Runtime": "python3.12", "Handler": "dispatcher.handler",
            # Do not reserve account concurrency. Some small AWS accounts must
            # retain ten unreserved executions and reject even a reservation of
            # one. Scheduler triggers are deliberately offset and dispatch_once
            # additionally checks the GitHub active run, writer lease and retry
            # ledger before it can create a workflow run.
            "Architectures": ["x86_64"], "MemorySize": 128, "Timeout": 90,
            "Role": {"Fn::GetAtt": ["DispatcherRole", "Arn"]}, "Code": {"S3Bucket": bucket, "S3Key": code_key},
            "Environment": {"Variables": {"STOCK_BUCKET": bucket, "GITHUB_PRIVATE_KEY_PARAMETER": private_key_parameter,
                "GITHUB_APP_ID": str(app_id), "GITHUB_INSTALLATION_ID": str(installation_id), "GITHUB_REPOSITORY_ID": str(repository_id)}}}},
        "ScheduleRole": {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": {"Ref": "AWS::AccountId"}},
                              "ArnEquals": {"aws:SourceArn": {"Fn::GetAtt": ["ScheduleGroup", "Arn"]}}}}]},
            "Policies": [{"PolicyName": "InvokeDispatcherOnly", "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": {"Fn::GetAtt": ["Dispatcher", "Arn"]}}]}}]}}
    }
    schedules = {"Opening": ("cron(50 9 * * ? *)", "opening"),
                 "Close": ("cron(30 15 * * ? *)", "close"),
                 "Catchup": ("cron(0 7 * * ? *)", "catchup"),
                 # Offset avoids throttling the precise triggers when reserved
                 # concurrency is one and Scheduler has retries disabled.
                 "Watchdog": ("cron(2/5 7-23 * * ? *)", "watchdog")}
    for name, (expression, phase) in schedules.items():
        resources[name + "Schedule"] = {"Type": "AWS::Scheduler::Schedule", "Properties": {
            "GroupName": {"Ref": "ScheduleGroup"}, "ScheduleExpression": expression,
            "ScheduleExpressionTimezone": "Asia/Shanghai", "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED" if enabled else "DISABLED",
            "Target": {"Arn": {"Fn::GetAtt": ["Dispatcher", "Arn"]},
                       "RoleArn": {"Fn::GetAtt": ["ScheduleRole", "Arn"]},
                       "Input": json.dumps({"phase": phase}, separators=(",", ":")),
                       "RetryPolicy": {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 0}}}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Stock exact-repository dispatch only; no collector compute",
            "Resources": resources, "Outputs": {
                "DispatcherArn": {"Value": {"Fn::GetAtt": ["Dispatcher", "Arn"]}},
                "ScheduleGroup": {"Value": {"Ref": "ScheduleGroup"}}}}


def build_zip(destination):
    """Build Linux wheels locally, never uploading or installing into the user's env."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Use a new artifact path")
    with tempfile.TemporaryDirectory(prefix="stock-dispatcher-") as folder:
        root = Path(folder)
        subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(root),
                        "--only-binary=:all:", "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
                        "--implementation", "cp", "--python-version", "3.12", "--abi", "cp312", "--abi", "abi3",
                        "cryptography==" + CRYPTOGRAPHY_VERSION], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
            archive.write(Path(__file__).with_name("dispatcher.py"), "dispatcher.py")
            for path in sorted(root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, str(path.relative_to(root)))
    return {"artifact": str(destination.resolve()), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "bytes": destination.stat().st_size, "cryptographyVersion": CRYPTOGRAPHY_VERSION}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-zip")
    parser.add_argument("--bucket")
    parser.add_argument("--code-key")
    parser.add_argument("--private-key-parameter")
    parser.add_argument("--app-id")
    parser.add_argument("--installation-id")
    parser.add_argument("--repository-id")
    parser.add_argument("--enabled", action="store_true")
    args = parser.parse_args()
    if args.build_zip:
        print(json.dumps(build_zip(args.build_zip), indent=2))
    else:
        print(json.dumps(template(args.bucket, args.code_key, args.private_key_parameter,
                                  args.app_id, args.installation_id, args.repository_id, args.enabled), indent=2))
