# The AI reasoning layer. Build the package first: ./scripts/package-lambda.sh
# (installs the Anthropic SDK into lambda/ai-analyst/build/).

data "archive_file" "analyst" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda/ai-analyst/build"
  output_path = "${path.module}/.build/ai-analyst.zip"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "analyst" {
  name               = "${local.name}-ai-analyst"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_cloudwatch_log_group" "analyst" {
  #checkov:skip=CKV_AWS_338:Demo environment keeps var.log_retention_days (14) to control cost
  name              = "/aws/lambda/${local.name}-ai-analyst"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.data.arn
}

resource "aws_iam_role_policy" "analyst" {
  role = aws_iam_role.analyst.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.analyst.arn}:*"
      },
      {
        Sid      = "QueryAppLogs"
        Effect   = "Allow"
        Action   = ["logs:StartQuery"]
        Resource = ["${aws_cloudwatch_log_group.workloads.arn}", "${aws_cloudwatch_log_group.workloads.arn}:*"]
      },
      {
        Sid      = "ReadQueryResults"
        Effect   = "Allow"
        Action   = ["logs:GetQueryResults", "logs:StopQuery"]
        Resource = "*"
      },
      {
        # Claude on Bedrock is served through the bedrock-mantle endpoint that the
        # Anthropic SDK's AnthropicBedrockMantle client calls (SigV4 service
        # "bedrock-mantle"). The bedrock:* actions cover the classic runtime path.
        # Tighten Resource to the specific model once you've confirmed it in IAM.
        Sid      = "InvokeClaude"
        Effect   = "Allow"
        Action   = ["bedrock-mantle:*", "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Sid      = "Tracing"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_lambda_function" "analyst" {
  #checkov:skip=CKV_AWS_117:Calls only AWS public APIs (CloudWatch Logs, Bedrock); a VPC adds NAT cost and no isolation benefit here
  #checkov:skip=CKV_AWS_116:Invoked synchronously by Mission Control; a DLQ only applies to async invocations
  #checkov:skip=CKV_AWS_272:Code signing is out of scope for the demo; package is built from the repo in CI
  #checkov:skip=CKV_AWS_173:Environment holds no secrets (log group name, model ID); AWS-managed encryption at rest applies
  function_name    = "${local.name}-ai-analyst"
  description      = "ARES Ops Copilot: CloudWatch + Prometheus + Kubernetes telemetry -> Claude on Bedrock"
  role             = aws_iam_role.analyst.arn
  runtime          = "python3.13"
  handler          = "handler.lambda_handler"
  architectures    = ["x86_64"]
  filename         = data.archive_file.analyst.output_path
  source_code_hash = data.archive_file.analyst.output_base64sha256
  memory_size      = 512
  timeout          = 120

  reserved_concurrent_executions = var.lambda_reserved_concurrency

  environment {
    variables = {
      APP_LOG_GROUP    = aws_cloudwatch_log_group.workloads.name
      BEDROCK_MODEL_ID = var.bedrock_model_id
      ANALYST_EFFORT   = var.analyst_effort
      WINDOW_MINUTES   = "15"
    }
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.analyst.name
  }

  depends_on = [aws_iam_role_policy.analyst]
}
