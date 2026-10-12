# One-time bootstrap, run from your laptop with admin credentials:
#   * S3 bucket for Terraform state (versioned, encrypted, TLS-only, S3-native locking)
#   * GitHub Actions OIDC identity provider (no long-lived AWS keys in GitHub)
#   * Three narrowly-trusted roles for the workflows:
#       plan  - read-only, assumable from pull requests
#       apply - infrastructure changes, only from the protected "aws-prod" environment
#       ecr   - push images to this project's ECR repositories only, only from main

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { Project = var.project, ManagedBy = "terraform", Stack = "bootstrap" } }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "ares-aiops"
}

variable "github_repository" {
  description = "owner/name of your GitHub repository, e.g. octocat/ares-aiops"
  type        = string
}

variable "github_environment" {
  description = "GitHub environment that gates terraform apply (add required reviewers to it)"
  type        = string
  default     = "aws-prod"
}

data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  state_bucket = "${var.project}-tfstate-${local.account_id}-${var.region}"
  oidc_host    = "token.actions.githubusercontent.com"
  repo_sub     = "repo:${var.github_repository}"
}

# ----------------------------------------------------------------- state bucket
resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_policy" "state_tls_only" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ----------------------------------------------------------------- GitHub OIDC
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  for_each = {
    plan  = ["${local.repo_sub}:pull_request", "${local.repo_sub}:ref:refs/heads/main"]
    apply = ["${local.repo_sub}:environment:${var.github_environment}"]
    ecr   = ["${local.repo_sub}:ref:refs/heads/main"]
  }
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = each.value
    }
  }
}

# plan: read-only + state read (PR plans run with -lock=false)
resource "aws_iam_role" "plan" {
  name                 = "${var.project}-gha-terraform-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# apply: creates VPC/EKS/IAM/KMS/Lambda. Gated by a protected GitHub environment
# with required reviewers. For production, replace with a scoped policy plus a
# permissions boundary on every role it creates.
resource "aws_iam_role" "apply" {
  name                 = "${var.project}-gha-terraform-apply"
  assume_role_policy   = data.aws_iam_policy_document.trust["apply"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "apply_admin" {
  role       = aws_iam_role.apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# ecr: push to this project's repositories only
resource "aws_iam_role" "ecr" {
  name                 = "${var.project}-gha-ecr-push"
  assume_role_policy   = data.aws_iam_policy_document.trust["ecr"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "ecr_push" {
  role = aws_iam_role.ecr.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
          "ecr:DescribeImages", "ecr:DescribeImageScanFindings", "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
        ]
        Resource = "arn:aws:ecr:${var.region}:${local.account_id}:repository/${var.project}/*"
      },
    ]
  })
}

# ----------------------------------------------------------------- outputs
output "state_bucket" { value = aws_s3_bucket.state.bucket }
output "plan_role_arn" { value = aws_iam_role.plan.arn }
output "apply_role_arn" { value = aws_iam_role.apply.arn }
output "ecr_push_role_arn" { value = aws_iam_role.ecr.arn }
output "backend_config" {
  value = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "${var.project}/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
