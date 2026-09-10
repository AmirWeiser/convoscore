import boto3

from app import config

_client_kwargs = dict(
    endpoint_url=config.AWS_ENDPOINT_URL,
    region_name=config.AWS_REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)


def s3_client():
    return boto3.client("s3", **_client_kwargs)


def sqs_client():
    return boto3.client("sqs", **_client_kwargs)
