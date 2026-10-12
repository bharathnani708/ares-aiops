# Customer-managed keys: one for Kubernetes secrets envelope encryption, one
# for CloudWatch Logs and ECR. Both rotate yearly.

data "aws_iam_policy_document" "eks_kms" {
  #checkov:skip=CKV_AWS_109:Key policy - Resource "*" means this key only; scoped to this account root
  #checkov:skip=CKV_AWS_111:Key policy - Resource "*" means this key only
  #checkov:skip=CKV_AWS_356:Key policy - Resource "*" means this key only
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_kms_key" "eks" {
  description             = "${local.name} EKS secrets encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.eks_kms.json
}

resource "aws_kms_alias" "eks" {
  name          = "alias/${local.name}-eks"
  target_key_id = aws_kms_key.eks.key_id
}

data "aws_iam_policy_document" "logs_kms" {
  #checkov:skip=CKV_AWS_109:Key policy - Resource "*" means this key only; admin statement is scoped to this account root
  #checkov:skip=CKV_AWS_111:Key policy - Resource "*" means this key only
  #checkov:skip=CKV_AWS_356:Key policy - Resource "*" means this key only
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }
  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "data" {
  description             = "${local.name} CloudWatch Logs and ECR encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.logs_kms.json
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}
