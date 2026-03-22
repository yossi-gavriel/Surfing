data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "app" {
  name        = "surf-ai-sg"
  description = "Security group for Surf AI System"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  nginx_conf = templatefile("${path.module}/nginx.conf", {
    domain_name = var.domain_name
  })

  enable_https = templatefile("${path.module}/enable_https.sh", {
    domain_name = var.domain_name
    admin_email = var.admin_email
  })
}

resource "aws_instance" "app_server" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.instance_type
  key_name      = var.key_name

  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2_profile.name

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/user_data.sh", {
    repo_url            = var.repo_url
    region              = var.aws_region
    bucket              = aws_s3_bucket.videos.id
    q_chunks            = aws_sqs_queue.video_chunks.url
    q_tracks            = aws_sqs_queue.tracks.url
    q_embed             = aws_sqs_queue.embeddings.url
    q_match             = aws_sqs_queue.matching.url
    q_clip              = aws_sqs_queue.clipper.url
    domain_name         = var.domain_name
    admin_email         = var.admin_email
    nginx_conf          = local.nginx_conf
    enable_https_script = local.enable_https
  })

  tags = {
    Name = "SurfAISystem"
  }
}
