terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# LocalStack, not real AWS - fake credentials are required by the provider's
# validation but never sent anywhere real. Only s3/sqs/iam are needed here, so
# only those endpoints are overridden.
provider "aws" {
  region                      = var.region
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    s3  = var.localstack_endpoint
    sqs = var.localstack_endpoint
    iam = var.localstack_endpoint
  }
}

resource "aws_s3_bucket" "conversations" {
  bucket = var.bucket_name
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days - default max, ample for a local demo
}

resource "aws_sqs_queue" "processing" {
  name                       = var.queue_name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  receive_wait_time_seconds  = 20 # long polling

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Lets the S3 bucket actually deliver ObjectCreated notifications to the queue.
resource "aws_sqs_queue_policy" "allow_s3_notify" {
  queue_url = aws_sqs_queue.processing.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.processing.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_s3_bucket.conversations.arn }
      }
    }]
  })
}

resource "aws_s3_bucket_notification" "incoming" {
  bucket = aws_s3_bucket.conversations.id

  queue {
    queue_arn     = aws_sqs_queue.processing.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "incoming/"
  }

  depends_on = [aws_sqs_queue_policy.allow_s3_notify]
}

# Least-privilege IAM policies. LocalStack Community does not enforce IAM at
# the API-call level - these exist to demonstrate the intended real-AWS shape
# (see DECISIONS.md), not to actually restrict access in this environment.
resource "aws_iam_policy" "api_write" {
  name = "convoscore-api-write"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = "${aws_s3_bucket.conversations.arn}/incoming/*"
    }]
  })
}

resource "aws_iam_policy" "worker_process" {
  name = "convoscore-worker-process"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.conversations.arn}/incoming/*"
      },
      {
        # ChangeMessageVisibility deliberately excluded - the worker relies
        # on natural visibility-timeout expiry (see DECISIONS.md), it never
        # calls this API. GetQueueUrl is separate from the queue's own ARN
        # scope (it's a resolve-by-name call), so it needs its own statement.
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.processing.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:GetQueueUrl"]
        Resource = aws_sqs_queue.processing.arn
      },
    ]
  })
}
