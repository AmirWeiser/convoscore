import boto3
from botocore.config import Config

from app import config

# Explicit, tight bounds - boto3's own defaults (60s connect, 60s read, and
# unbounded internal retries) were found to let the worker's single-threaded
# loop stall for a very long time against a loaded/degraded LocalStack
# instance, with nothing to detect it (a TCP liveness check on the metrics
# port stays open regardless). See DECISIONS.md.
# read_timeout=30 (not tighter) is deliberate: SQS long-polling uses
# WaitTimeSeconds=20 (see worker.py), and the client-side read timeout must
# stay comfortably above that or every single receive_message call would
# spuriously time out.
_boto_config = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2})

_client_kwargs = dict(
    endpoint_url=config.AWS_ENDPOINT_URL,
    region_name=config.AWS_REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
    config=_boto_config,
)


def s3_client():
    return boto3.client("s3", **_client_kwargs)


def sqs_client():
    return boto3.client("sqs", **_client_kwargs)
