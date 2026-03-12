locals {
  stack_name = "ds5220-dp1-terraform"
  bucket_name = "${var.uva_id}-ds5220-dp1"
}

# Variables
variable "instance_type" {
  description = "The EC2 instance type"
  type = string
  default = "t3.micro"
}

variable "key_name" {
  description = "Name of an existing EC2 key pair to allow SSH access"
  type = string
}

variable "uva_id" {
  description = "Your UVA computing ID (to name the bucket)"
  type = string
}

variable "ssh_location" {
  description = "Copy your IP from https://checkip.amazonaws.com and add /32 (e.g., 1.2.3.4/32)"
  type = string
  default = "0.0.0.0/0"
}


# Resources

resource "aws_sns_topic" "sns_topic" {
  name = "ds5220-dp1"
}

resource "aws_sns_topic_policy" "sns_topic_policy" {
  arn = aws_sns_topic.sns_topic.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
        Effect = "Allow"
        Principal = {Service = "s3.amazonaws.com"}
        Action = "sns:Publish"
        Resource = aws_sns_topic.sns_topic.arn
        Condition = {
          ArnLike = {"aws:SourceArn" = "arn:aws:s3:::${var.uva_id}-ds5220-dp1"}
        }
      }]
  })
}

resource "aws_s3_bucket" "s3_bucket" {
  bucket = "${var.uva_id}-ds5220-dp1"
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = aws_s3_bucket.s3_bucket.id

  topic {
    topic_arn     = aws_sns_topic.sns_topic.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/"
    filter_suffix = ".csv"
  }
}

resource "aws_security_group" "ec2_security_group" {
  description = "Allows SSH and FastAPI access"
  name = "${local.stack_name}-ec2-sg"
  ingress {
    protocol = "tcp"
    from_port = 8000
    to_port = 8000
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow FastAPI access"
    }
   
  ingress {
    protocol = "tcp"
    from_port = 22
    to_port = 22
    cidr_blocks = [var.ssh_location]
    description = "Allow SSH access"
    }
  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}

resource "aws_iam_role" "ec2_instance_role" {
  name = "${local.stack_name}-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
        Effect = "Allow"
        Principal = {Service = "ec2.amazonaws.com"}
        Action = "sts:AssumeRole"
      }]
  })
}

resource "aws_iam_role_policy" "s3_access" {
  name = "S3FullAccess"
  role = aws_iam_role.ec2_instance_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
      Resource = [
        "arn:aws:s3:::${local.bucket_name}",
        "arn:aws:s3:::${local.bucket_name}/*"
      ]
    }]
  })
}

resource "aws_iam_instance_profile" "ec2_instance_profile" {
  name = "${local.stack_name}-instance-profile"  
  role = aws_iam_role.ec2_instance_role.name
}

resource "aws_instance" "ec2_instance" {
  ami                  = "ami-0b6c6ebed2801a5cb" 
  instance_type        = var.instance_type
  key_name             = var.key_name
  iam_instance_profile = aws_iam_instance_profile.ec2_instance_profile.name
  vpc_security_group_ids = [aws_security_group.ec2_security_group.id]
  user_data = <<-EOF
              #!/bin/bash
              export BUCKET_NAME=${var.uva_id}-ds5220-dp1
              echo "export BUCKET_NAME=${var.uva_id}-ds5220-dp1" >> /etc/environment

              apt-get update -y
              apt-get install -y git python3 python3-pip python3-venv
          
              cd /home/ubuntu
              git clone https://github.com/randa-ama/anomaly-detection.git
              cd anomaly-detection

              python3 -m venv venv
              source venv/bin/activate
              ./venv/bin/pip install -r requirements.txt

              chown -R ubuntu:ubuntu /home/ubuntu/anomaly-detection
              ./venv/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 &
              EOF

  tags = { Name = "${local.stack_name}-ec2-instance" }
}

resource "aws_eip" "elastic_ip" {
  instance = aws_instance.ec2_instance.id
  domain   = "vpc"
}


output "app_url" {
  value = "http://${aws_eip.elastic_ip.public_ip}:8000/notify"
}

output "bucket_name" {
  value = aws_s3_bucket.s3_bucket.id
}
