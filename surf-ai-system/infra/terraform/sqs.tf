resource "aws_sqs_queue" "video_chunks" {
  name                       = "video-chunks-queue"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400
}

resource "aws_sqs_queue" "tracks" {
  name                       = "tracks-queue"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400
}

resource "aws_sqs_queue" "embeddings" {
  name                       = "embeddings-queue"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400
}

resource "aws_sqs_queue" "matching" {
  name                       = "matching-queue"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400
}

resource "aws_sqs_queue" "clipper" {
  name                       = "clipper-queue"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400
}

resource "aws_sqs_queue" "analysis" {
  name                       = "analysis-queue"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.analysis_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "analysis_dlq" {
  name                       = "analysis-dlq"
  message_retention_seconds  = 604800
}
