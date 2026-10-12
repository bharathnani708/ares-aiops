locals {
  images = ["habitat-api", "mission-control"]
}

resource "aws_ecr_repository" "app" {
  for_each             = toset(local.images)
  name                 = "${local.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE" # a tag always means the same bytes
  force_delete         = true        # lets `terraform destroy` clean up the demo

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 20 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}
