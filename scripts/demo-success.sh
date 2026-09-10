#!/usr/bin/env bash
# Reproducible success demo against the deployed service. Requires
# `kubectl port-forward svc/convoscore-api 8080:8000` running separately.
# Uses python3 (standard on Linux/Mac; on Windows this may need to be `py`
# depending on local setup - see README).
set -euo pipefail

API=http://localhost:8080
PY=${PYTHON_BIN:-python3}

echo "=== Direct API ingestion ==="
RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"Demo: customer is happy with the fast support response."}')
echo "$RESP"
API_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$RESP")

echo ""
echo "=== Direct S3 ingestion (no API call) ==="
S3_KEY=$("$PY" -c "
import boto3, json, uuid
s3 = boto3.client('s3', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
key = f'incoming/demo-s3-{uuid.uuid4()}.json'
s3.put_object(Bucket='convoscore-conversations', Key=key, Body=json.dumps({'text': 'Demo: customer reports the checkout page is much faster now.'}))
print(key)
")
echo "uploaded $S3_KEY"

echo ""
echo "=== Waiting for the API-submitted conversation to complete (real OpenAI scoring) ==="
until curl -s "$API/conversations/$API_ID" | grep -q 'status-completed'; do sleep 3; done
echo "API-submitted $API_ID: completed"

echo ""
echo "Done. The S3-submitted row (source_key: $S3_KEY) will complete shortly too -"
echo "check $API/conversations to see both."
