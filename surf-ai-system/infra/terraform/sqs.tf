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
