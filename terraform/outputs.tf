output "bucket_name" {
  value = aws_s3_bucket.conversations.id
}

output "queue_url" {
  value = aws_sqs_queue.processing.id
}

output "queue_arn" {
  value = aws_sqs_queue.processing.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.id
}
