# The Control Room on AWS: the tower runtime as its own function (one writer at a time), the console
# page and its API on the same HTTP API (reads open, writes behind the key authorizer), two schedules on
# the company's own wall clock (the Monday run and the SLA tick), a custom event bus for carrier and data
# feeds, and an SNS topic for the approval emails. Applied together with main.tf.

variable "tower_week" { default = "12" }                # the week the Monday run plays on the canonical data
variable "tower_timezone" { default = "Asia/Kolkata" }  # the company's clock: Europe/Amsterdam, Europe/London ...
variable "monday_cron" { default = "cron(30 6 ? * MON *)" } # 06:30 on Monday, in tower_timezone (summer time handled)
variable "tick_rate" { default = "rate(30 minutes)" }   # how often the SLA clock looks at the plans
variable "tick_enabled" { default = true }
# A new account may have only 10 concurrent executions in total, and AWS keeps 10 unreserved, so a
# reservation of 1 fails there. The handler takes a DynamoDB lock instead; set 1 here only if your
# account's limit is higher (aws lambda get-account-settings).
variable "tower_reserved" { default = -1 }

resource "aws_sns_topic" "approvals" {
  name = "gfb-tower-approvals"
}

resource "aws_iam_role_policy" "tower" {
  name = "gfb-foundry-tower"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/tower/*" },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.approvals.arn },
      # a console click is queued: the function invokes itself asynchronously (handler.py, _console_enqueue)
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.tower.arn },
    ]
  })
}

resource "aws_lambda_function" "tower" {
  function_name                  = local.fn_tower
  role                           = aws_iam_role.lambda.arn
  runtime                        = "python3.13"
  handler                        = "deploy.handler.tower_handler"
  filename                       = data.archive_file.code.output_path
  source_code_hash               = data.archive_file.code.output_base64sha256
  timeout                        = 900 # a live Monday makes dozens of model calls; Scheduler waits
  memory_size                    = 1024
  reserved_concurrent_executions = var.tower_reserved # -1: none; one writer at a time comes from a DynamoDB lock
  layers                         = [aws_lambda_layer_version.deps.arn]
  environment {
    variables = {
      FOUNDRY_MODEL        = var.model_id
      FOUNDRY_PROFILE      = var.profile_name
      FOUNDRY_BUDGET_USD   = var.budget_usd
      FOUNDRY_DAILY_USD    = var.daily_usd
      FOUNDRY_FAKE         = var.fake
      FOUNDRY_PANEL_TABLE  = aws_dynamodb_table.panel.name
      FOUNDRY_PANEL_ID     = var.panel_id
      FOUNDRY_DATA_URI     = var.data_uri
      FOUNDRY_DATA_BUCKET  = aws_s3_bucket.data.bucket
      FOUNDRY_SNS_TOPIC    = aws_sns_topic.approvals.arn
      FOUNDRY_CONSOLE_URL  = aws_apigatewayv2_api.http.api_endpoint
      FOUNDRY_TOWER_WEEK   = var.tower_week
      FOUNDRY_AUDIT_STDOUT = "1"
    }
  }
  depends_on = [aws_cloudwatch_log_group.tower]
}

resource "aws_apigatewayv2_integration" "tower" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.tower.invoke_arn
  payload_format_version = "2.0"
}

# The page and the reads are open (the data is made up); every write passes the key authorizer.
resource "aws_apigatewayv2_route" "tower_read" {
  for_each  = toset(["GET /", "GET /api/{proxy+}"])
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.tower.id}"
}

resource "aws_apigatewayv2_route" "tower_write" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "POST /api/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.tower.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.key.id
}

resource "aws_lambda_permission" "tower_apigw" {
  statement_id  = "AllowAPIGatewayInvokeTower"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.tower.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# ------------------------------------------------------------------ the schedules, on the company's clock
# EventBridge Scheduler takes a time zone, so "06:30 on Monday" stays 06:30 local when Europe changes
# its clocks (a plain EventBridge rule is UTC only). It delivers at least once: the runtime's keys make a
# repeat harmless. The Monday run archives last week's state and starts the week fresh.
resource "aws_iam_role" "scheduler" {
  name               = "gfb-foundry-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "gfb-foundry-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.tower.arn }] })
}

resource "aws_scheduler_schedule" "monday" {
  name                         = "gfb-tower-monday"
  schedule_expression          = var.monday_cron
  schedule_expression_timezone = var.tower_timezone
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.tower.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ tower = "monday", fresh = true })
    retry_policy { maximum_retry_attempts = 2 }
  }
}

resource "aws_scheduler_schedule" "tick" {
  name                         = "gfb-tower-sla-tick"
  schedule_expression          = var.tick_rate
  schedule_expression_timezone = var.tower_timezone
  state                        = var.tick_enabled ? "ENABLED" : "DISABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.tower.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ tower = "tick" })
    retry_policy { maximum_retry_attempts = 0 } # a missed tick is caught by the next one
  }
}

# ------------------------------------------------------------------ carrier and data feeds
# Feeds publish here, e.g.
#   aws events put-events --entries '[{"EventBusName":"gfb-tower","Source":"gfb.carrier","DetailType":"eta.updated",
#     "Detail":"{\"week\":12,\"dc\":\"CHN\",\"sku\":\"MN-1L\",\"eta_days\":5,\"carrier\":\"Sahyadri Roadways\"}"}]'
resource "aws_cloudwatch_event_bus" "tower" {
  name = "gfb-tower"
}

resource "aws_cloudwatch_event_rule" "feeds" {
  name           = "gfb-tower-feeds"
  event_bus_name = aws_cloudwatch_event_bus.tower.name
  event_pattern = jsonencode({
    source        = ["gfb.carrier", "gfb.data"],
    "detail-type" = ["eta.updated", "lane.delayed", "transfer.delayed", "snapshot.refreshed"]
  })
}

resource "aws_cloudwatch_event_target" "feeds" {
  rule           = aws_cloudwatch_event_rule.feeds.name
  event_bus_name = aws_cloudwatch_event_bus.tower.name
  arn            = aws_lambda_function.tower.arn
}

resource "aws_lambda_permission" "feeds" {
  statement_id  = "AllowEventBridgeFeeds"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.tower.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.feeds.arn
}

resource "aws_cloudwatch_metric_alarm" "tower_errors" {
  alarm_name          = "gfb-foundry-tower-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.tower.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}

output "console_url" { value = aws_apigatewayv2_api.http.api_endpoint }
output "approvals_topic" { value = aws_sns_topic.approvals.arn }
