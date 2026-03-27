output "ec2_instance_id" {
  description = "EC2 Instance ID (used by scripts/dev.sh for start/stop)"
  value       = aws_instance.app_server.id
}

output "elastic_ip" {
  description = "Static Elastic IP address"
  value       = aws_eip.app.public_ip
}

output "s3_bucket" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.videos.id
}

output "aws_region" {
  description = "AWS region"
  value       = var.aws_region
}

output "domain_url" {
  description = "Application URL (run enable_https.sh first for HTTPS)"
  value       = "https://${var.domain_name}"
}

output "ssh_command" {
  description = "SSH command to connect to the server"
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_eip.app.public_ip}"
}

output "start_stop_instructions" {
  description = "Cost optimization: start/stop the EC2 instance"
  value       = <<-EOT
    Manual:    ./scripts/dev.sh up / down / status
    Auto:      EC2 starts at 06:00 Israel time, stops when queues drain after 18:00
    Watchdog:  journalctl -u surf-ai-watchdog -f   (on EC2)
    Deploy:    ./scripts/deploy_watchdog.sh         (first time only)
  EOT
}

output "lambda_start_ec2_arn" {
  description = "ARN of the Lambda that starts the EC2"
  value       = aws_lambda_function.start_ec2.arn
}

output "lambda_stop_ingestion_arn" {
  description = "ARN of the Lambda that stops ingestion (triggers watchdog shutdown)"
  value       = aws_lambda_function.stop_ingestion.arn
}

output "schedule_start" {
  description = "CloudWatch cron that starts EC2"
  value       = var.schedule_start
}

output "schedule_stop" {
  description = "CloudWatch cron that stops ingestion"
  value       = var.schedule_stop
}
