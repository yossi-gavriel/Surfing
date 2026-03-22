variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.large"
}

variable "key_name" {
  description = "SSH Key Pair name (must already exist in AWS)"
  type        = string
}

variable "repo_url" {
  description = "Git repository URL to clone on the server"
  type        = string
  default     = "https://github.com/yossi-gavriel/Surfing.git"
}

variable "domain_name" {
  description = "Domain name for the application"
  type        = string
  default     = "surfing.heyi.co.il"
}

variable "admin_email" {
  description = "Admin email for Let's Encrypt SSL certificate"
  type        = string
  default     = "admin@heyi.co.il"
}

variable "hosted_zone_name" {
  description = "Existing Route53 hosted zone name (must already exist)"
  type        = string
  default     = "heyi.co.il"
}

variable "vpc_id" {
  description = "VPC ID to deploy into"
  type        = string
  default     = "vpc-08ddaa3bd6694a3cd"
}

variable "subnet_id" {
  description = "Public subnet ID for the EC2 instance"
  type        = string
  default     = "subnet-06e1254c339d302e7"
}
