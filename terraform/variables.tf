variable "region" {
  type    = string
  default = "us-east-1"
}

variable "localstack_endpoint" {
  type    = string
  default = "http://localhost:4566"
}

variable "bucket_name" {
  type    = string
  default = "convoscore-conversations"
}

variable "queue_name" {
  type    = string
  default = "convoscore-processing"
}

variable "visibility_timeout_seconds" {
  description = "Must stay equal to the worker's stale-reclaim threshold (app/worker.py) - one number, two places."
  type        = number
  default     = 60
}

variable "max_receive_count" {
  type    = number
  default = 3
}
