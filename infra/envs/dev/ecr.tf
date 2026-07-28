# probe/cut/finish share this one image (same digest, different handler CMD
# per function); the Fargate renderer keeps its own "render-task" image.
resource "aws_ecr_repository" "lambda" {
  name                 = "${var.project_name}/lambda"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # disposable dev infra -- destroy must not be blocked by pushed images

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "render_task" {
  name                 = "${var.project_name}/render-task"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Whisper gets its own image -- faster-whisper + baked model weights are
# multi-hundred-MB and irrelevant to every other Lambda's cold start.
resource "aws_ecr_repository" "analyze_transcribe" {
  name                 = "${var.project_name}/analyze-transcribe"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = {
    lambda             = aws_ecr_repository.lambda.name,
    render_task        = aws_ecr_repository.render_task.name,
    analyze_transcribe = aws_ecr_repository.analyze_transcribe.name,
  }
  repository = each.value

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "keep only the last 10 tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 10
        }
        action = { type = "expire" }
      }
    ]
  })
}
