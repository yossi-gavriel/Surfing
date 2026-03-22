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
    Start:  ./scripts/dev.sh up
    Stop:   ./scripts/dev.sh down
    Status: ./scripts/dev.sh status
  EOT
}
