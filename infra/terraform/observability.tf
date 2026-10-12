# Application logs: Fluent Bit (namespace logging) ships container logs from
# the "ares" namespace into this log group. The analyst Lambda queries it.
resource "aws_cloudwatch_log_group" "workloads" {
  name              = "/aws/eks/${local.name}/workloads"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.data.arn
}

# ----------------------------------------------------------------- Pod Identity
data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# Fluent Bit: may only write streams into the workloads log group.
resource "aws_iam_role" "fluent_bit" {
  name               = "${local.name}-fluent-bit"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy" "fluent_bit" {
  role = aws_iam_role.fluent_bit.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = ["${aws_cloudwatch_log_group.workloads.arn}", "${aws_cloudwatch_log_group.workloads.arn}:*"]
    }]
  })
}

resource "aws_eks_pod_identity_association" "fluent_bit" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "logging"
  service_account = "fluent-bit"
  role_arn        = aws_iam_role.fluent_bit.arn
}

# Mission Control: may only invoke the analyst Lambda.
resource "aws_iam_role" "mission_control" {
  name               = "${local.name}-mission-control"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy" "mission_control" {
  role = aws_iam_role.mission_control.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.analyst.arn
    }]
  })
}

resource "aws_eks_pod_identity_association" "mission_control" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "ares"
  service_account = "mission-control"
  role_arn        = aws_iam_role.mission_control.arn
}
