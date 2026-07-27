resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

# without this, runTask.sync rejects any CapacityProviderStrategy naming
# FARGATE/FARGATE_SPOT with ECS.InvalidParameterException
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_cloudwatch_log_group" "render" {
  name              = "/ecs/${local.name_prefix}-render"
  retention_in_days = 14
}

data "aws_ecr_image" "render_task" {
  repository_name = aws_ecr_repository.render_task.name
  image_tag       = var.image_tag
}

locals {
  render_image_uri = "${aws_ecr_repository.render_task.repository_url}@${data.aws_ecr_image.render_task.image_digest}"
}

# single task def -- the two Render states in the state machine pick Spot vs
# on-demand only via CapacityProviderStrategy at runTask time
resource "aws_ecs_task_definition" "render" {
  family                   = "${local.name_prefix}-render"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "4096"
  memory                   = "8192"
  execution_role_arn       = aws_iam_role.render_task_execution.arn
  task_role_arn            = aws_iam_role.render_task.arn

  container_definitions = jsonencode([
    {
      name      = "render"
      image     = local.render_image_uri
      essential = true
      environment = [
        { name = "JOBS_TABLE", value = aws_dynamodb_table.jobs.name },
        { name = "WORK_BUCKET", value = aws_s3_bucket.this["work"].bucket },
        { name = "OUTPUT_BUCKET", value = aws_s3_bucket.this["output"].bucket },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.render.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "render"
        }
      }
    }
  ])
}
