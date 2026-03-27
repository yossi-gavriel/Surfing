# ────────────────────────────────────────────────────────────────────────────
# Surf AI — On-Demand Execution: Lambda + CloudWatch Schedules
#
# Architecture:
#   CloudWatch cron(6am) → Lambda start-ec2  → EC2 starts → all services up
#   CloudWatch cron(6pm) → Lambda stop-ingest → SSM → docker stop ingestion
#                                                       watchdog drains queues
#                                                       watchdog stops EC2
# ────────────────────────────────────────────────────────────────────────────

# ── Zip Lambda source files ──────────────────────────────────────────────────
data "archive_file" "start_ec2" {
  type        = "zip"
  source_file = "${path.module}/lambdas/start_ec2.py"
  output_path = "${path.module}/lambdas/start_ec2.zip"
}

data "archive_file" "stop_ingestion" {
  type        = "zip"
  source_file = "${path.module}/lambdas/stop_ingestion.py"
  output_path = "${path.module}/lambdas/stop_ingestion.zip"
}

# ── Lambda: start EC2 ────────────────────────────────────────────────────────
resource "aws_lambda_function" "start_ec2" {
  function_name    = "surf-ai-start-ec2"
  filename         = data.archive_file.start_ec2.output_path
  source_code_hash = data.archive_file.start_ec2.output_base64sha256
  role             = aws_iam_role.lambda_scheduler.arn
  handler          = "start_ec2.handler"
  runtime          = "python3.12"
  timeout          = 30

  environment {
    variables = {
      INSTANCE_ID = aws_instance.app_server.id
    }
  }

  tags = { Name = "SurfAIStartEC2" }
}

# ── Lambda: stop ingestion (triggers watchdog drain + auto-shutdown) ──────────
resource "aws_lambda_function" "stop_ingestion" {
  function_name    = "surf-ai-stop-ingestion"
  filename         = data.archive_file.stop_ingestion.output_path
  source_code_hash = data.archive_file.stop_ingestion.output_base64sha256
  role             = aws_iam_role.lambda_scheduler.arn
  handler          = "stop_ingestion.handler"
  runtime          = "python3.12"
  timeout          = 30

  environment {
    variables = {
      INSTANCE_ID = aws_instance.app_server.id
    }
  }

  tags = { Name = "SurfAIStopIngestion" }
}

# ── CloudWatch Event Rules ────────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "start_ec2" {
  name                = "surf-ai-start-ec2"
  description         = "Start Surf AI EC2 at dawn (Israel time = UTC+3 in summer)"
  schedule_expression = var.schedule_start
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_rule" "stop_ingestion" {
  name                = "surf-ai-stop-ingestion"
  description         = "Stop ingestion at end of surf day — watchdog handles shutdown"
  schedule_expression = var.schedule_stop
  state               = "ENABLED"
}

# ── CloudWatch → Lambda targets ───────────────────────────────────────────────
resource "aws_cloudwatch_event_target" "start_ec2" {
  rule      = aws_cloudwatch_event_rule.start_ec2.name
  target_id = "StartEC2"
  arn       = aws_lambda_function.start_ec2.arn
}

resource "aws_cloudwatch_event_target" "stop_ingestion" {
  rule      = aws_cloudwatch_event_rule.stop_ingestion.name
  target_id = "StopIngestion"
  arn       = aws_lambda_function.stop_ingestion.arn
}

# ── Permissions: CloudWatch can invoke Lambdas ────────────────────────────────
resource "aws_lambda_permission" "start_ec2_cw" {
  statement_id  = "AllowCloudWatchInvokeStart"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.start_ec2.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.start_ec2.arn
}

resource "aws_lambda_permission" "stop_ingestion_cw" {
  statement_id  = "AllowCloudWatchInvokeStop"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stop_ingestion.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.stop_ingestion.arn
}
