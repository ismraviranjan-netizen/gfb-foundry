# gfb-foundry: the agents and the tower behind one HTTP API on AWS (ap-south-1).
# Lambda (Python 3.13) + API Gateway HTTP API (a key authorizer on every call that can spend or change)
# + DynamoDB (the levers panel, the daily spend ledger and the tower's lock) + S3 (the company's tables, the
# tower's state, the agents' plans live in it) + Bedrock through the global Sonnet 4.6 profile, with a three-part least-privilege policy.
# Apply from deploy/:  terraform init && terraform plan && terraform apply
terraform {
  required_version = ">= 1.5"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.23, < 7.0" } # 6.23+ reads `aws login` credentials
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

variable "region" { default = "ap-south-1" }
variable "model_id" { default = "global.anthropic.claude-sonnet-4-6" } # a global inference profile id
variable "profile_name" { default = "gfb" }
variable "budget_usd" { default = "0.25" } # one run's ceiling (the agent stops itself)
variable "daily_usd" { default = "2.00" }  # the day's ceiling (the handler refuses, HTTP 429)
variable "fake" { default = "0" }          # "1" = the scripted brain, for the first smoke test
variable "data_uri" { default = "canon:" } # "canon:" = the generated week; "s3://<bucket>/<prefix>" = a company's CSVs
variable "panel_id" { default = "gfb" }
variable "log_days" { default = 14 }        # CloudWatch keeps two weeks: enough to debug, cheap to keep
variable "throttle_rate" { default = 5 }    # requests per second across the API
variable "throttle_burst" { default = 10 }
variable "alert_email" { default = "" }     # set it to get alarm emails (confirm the first SNS email)

provider "aws" {
  region = var.region
  default_tags { tags = { project = "gfb-foundry" } }
}

data "aws_caller_identity" "me" {}

locals {
  account     = data.aws_caller_identity.me.account_id
  model_name  = trimprefix(var.model_id, "global.")   # anthropic.claude-sonnet-4-6
  profile_arn = "arn:aws:bedrock:${var.region}:${local.account}:inference-profile/${var.model_id}"
  fn_rca      = "gfb-foundry-rca"
  fn_tower    = "gfb-foundry-tower"
  fn_auth     = "gfb-foundry-authorizer"
}

# The code is zipped from a whitelist built by `make bundle` (deploy/bundle.py): foundry/, profiles/,
# networks/ and deploy/handler.py. Zipping the repo root would also zip terraform.tfstate, which holds the
# console key in plain text, the moment it exists.
data "archive_file" "code" {
  type        = "zip"
  source_dir  = "${path.module}/build/bundle"
  output_path = "${path.module}/build/foundry.zip"
}

# ------------------------------------------------------------------ logs: one group per function, kept two weeks
resource "aws_cloudwatch_log_group" "rca" {
  name              = "/aws/lambda/${local.fn_rca}"
  retention_in_days = var.log_days
}
resource "aws_cloudwatch_log_group" "tower" {
  name              = "/aws/lambda/${local.fn_tower}"
  retention_in_days = var.log_days
}
resource "aws_cloudwatch_log_group" "auth" {
  name              = "/aws/lambda/${local.fn_auth}"
  retention_in_days = var.log_days
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/gfb-foundry"
  retention_in_days = var.log_days
}

# ------------------------------------------------------------------ the agents' role: only what they use
resource "aws_iam_role" "lambda" {
  name               = "gfb-foundry-lambda"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "lambda" {
  name = "gfb-foundry-lambda"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Sid = "Logs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource = ["${aws_cloudwatch_log_group.rca.arn}:*", "${aws_cloudwatch_log_group.tower.arn}:*"] },
      # Global cross-Region inference needs three grants (Bedrock user guide, "Global cross-Region inference"):
      # 1. the global profile in this Region, 2. the model in this Region through that profile,
      # 3. the model with no Region ("unspecified"), where the global route sends the request.
      { Sid = "ProfileInRegion", Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      Resource = local.profile_arn },
      { Sid = "ModelInRegion", Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        Resource  = "arn:aws:bedrock:${var.region}::foundation-model/${local.model_name}",
      Condition = { StringEquals = { "aws:RequestedRegion" = var.region, "bedrock:InferenceProfileArn" = local.profile_arn } } },
      { Sid = "ModelGlobal", Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        Resource  = "arn:aws:bedrock:::foundation-model/${local.model_name}",
      Condition = { StringEquals = { "aws:RequestedRegion" = "unspecified", "bedrock:InferenceProfileArn" = local.profile_arn } } },
      { Sid = "Tables", Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
      Resource = aws_dynamodb_table.panel.arn },
      { Sid = "CompanyData", Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"],
      Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"] },
      # every proposal an agent makes is copied here before the answer goes back (/tmp dies with the container)
      { Sid = "Proposals", Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/proposals/*" },
    ]
  })
}

# The levers panel (one item, read before every tool call) and the spend ledger (one item per UTC day,
# "spend#2026-11-08", updated with an atomic ADD) share this table.
resource "aws_dynamodb_table" "panel" {
  name         = "gfb-foundry-panel"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "panel_id"
  attribute {
    name = "panel_id"
    type = "S"
  }
}

resource "aws_dynamodb_table_item" "panel_default" {
  table_name = aws_dynamodb_table.panel.name
  hash_key   = aws_dynamodb_table.panel.hash_key
  item       = jsonencode({ panel_id = { S = var.panel_id }, kill_switch = { BOOL = false }, propose_only = { BOOL = true }, disabled_tools = { L = [] } })
  lifecycle { ignore_changes = [item] } # the operator flips levers; Terraform must not flip them back
}

# ------------------------------------------------------------------ the bucket: private, versioned (the tower's state can be rolled back)
resource "aws_s3_bucket" "data" {
  bucket_prefix = "gfb-foundry-data-"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
  rule { # the console's queued clicks: the page reads an answer within minutes, so a week is plenty
    id     = "console-jobs"
    status = "Enabled"
    filter { prefix = "tower/jobs/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }
  depends_on = [aws_s3_bucket_versioning.data]
}

# ------------------------------------------------------------------ the agents' function
resource "aws_lambda_function" "rca" {
  function_name    = local.fn_rca
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  handler          = "deploy.handler.lambda_handler"
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 120
  memory_size      = 512
  layers           = [aws_lambda_layer_version.deps.arn]
  environment {
    variables = {
      FOUNDRY_MODEL           = var.model_id
      FOUNDRY_PROFILE         = var.profile_name
      FOUNDRY_BUDGET_USD      = var.budget_usd
      FOUNDRY_DAILY_USD       = var.daily_usd
      FOUNDRY_FAKE            = var.fake
      FOUNDRY_PANEL_TABLE     = aws_dynamodb_table.panel.name
      FOUNDRY_PANEL_ID        = var.panel_id
      FOUNDRY_DATA_URI        = var.data_uri
      FOUNDRY_DATA_BUCKET     = aws_s3_bucket.data.bucket
      FOUNDRY_AUDIT_STDOUT    = "1"
    }
  }
  depends_on = [aws_cloudwatch_log_group.rca]
}

# PyYAML and tzdata are the third-party imports; boto3 ships in the runtime. Build once: make layer
resource "aws_lambda_layer_version" "deps" {
  layer_name          = "gfb-foundry-deps"
  filename            = "${path.module}/build/layer.zip"
  source_code_hash    = filebase64sha256("${path.module}/build/layer.zip")
  compatible_runtimes = ["python3.13"]
}

# ------------------------------------------------------------------ the key authorizer
resource "random_password" "console_key" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "console_key" {
  name  = "/gfb-foundry/console-key"
  type  = "SecureString" # encrypted with the AWS managed key aws/ssm: no key to manage, no charge
  value = random_password.console_key.result
}

resource "aws_iam_role" "auth" {
  name               = "gfb-foundry-authorizer"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy" "auth" {
  name = "gfb-foundry-authorizer"
  role = aws_iam_role.auth.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.auth.arn}:*" },
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = aws_ssm_parameter.console_key.arn },
    ]
  })
}

data "archive_file" "auth" {
  type        = "zip"
  source_file = "${path.module}/authorizer.py"
  output_path = "${path.module}/build/authorizer.zip"
}

resource "aws_lambda_function" "auth" {
  function_name    = local.fn_auth
  role             = aws_iam_role.auth.arn
  runtime          = "python3.13"
  handler          = "authorizer.handler"
  filename         = data.archive_file.auth.output_path
  source_code_hash = data.archive_file.auth.output_base64sha256
  timeout          = 5
  memory_size      = 128
  environment {
    variables = {
      TOWER_KEY_PARAM = aws_ssm_parameter.console_key.name
      KEY_VERSION     = substr(sha256(random_password.console_key.result), 0, 12) # a rotation changes the config: warm containers retire
    }
  }
  depends_on = [aws_cloudwatch_log_group.auth]
}

# ------------------------------------------------------------------ the HTTP API
resource "aws_apigatewayv2_api" "http" {
  name          = "gfb-foundry"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_authorizer" "key" {
  api_id                            = aws_apigatewayv2_api.http.id
  name                              = "tower-key"
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.auth.invoke_arn
  identity_sources                  = ["$request.header.x-tower-key"] # no header: 401 before any Lambda runs
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true
  authorizer_result_ttl_in_seconds  = 300
}

resource "aws_lambda_permission" "auth" {
  statement_id  = "AllowAPIGatewayInvokeAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/authorizers/${aws_apigatewayv2_authorizer.key.id}"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.rca.invoke_arn
  payload_format_version = "2.0"
}

# Every agent route can reach Bedrock, so every one passes the key authorizer. /health stays open.
resource "aws_apigatewayv2_route" "agents" {
  for_each           = toset(["POST /diagnose", "POST /sweep", "POST /rebalance", "POST /sense", "POST /queue", "POST /plan", "POST /proposal"])
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.key.id
}

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  default_route_settings { # a loop calling the URL meets the throttle before it meets the bill
    throttling_rate_limit  = var.throttle_rate
    throttling_burst_limit = var.throttle_burst
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({ request = "$context.requestId", ip = "$context.identity.sourceIp", route = "$context.routeKey",
    status = "$context.status", ms = "$context.responseLatency", auth = "$context.authorizer.error" })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rca.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# ------------------------------------------------------------------ alarms, to your inbox if alert_email is set
resource "aws_sns_topic" "ops" {
  name = "gfb-foundry-ops"
}
resource "aws_sns_topic_subscription" "ops_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "gfb-foundry-rca-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.rca.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}

# The handlers answer a failure with a 502 instead of crashing (no stack trace to a caller), so Lambda's own
# Errors metric never counts it. Each one also writes {"audit": "handler.error"}: this filter counts those.
resource "aws_cloudwatch_log_metric_filter" "handled" {
  for_each       = { rca = aws_cloudwatch_log_group.rca.name, tower = aws_cloudwatch_log_group.tower.name }
  name           = "gfb-foundry-${each.key}-handled-errors"
  log_group_name = each.value
  pattern        = "{ $.audit = \"handler.error\" }"
  metric_transformation {
    name      = "HandledErrors"
    namespace = "GfbFoundry"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "handled" {
  alarm_name          = "gfb-foundry-handled-errors"
  namespace           = "GfbFoundry"
  metric_name         = "HandledErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}

output "endpoint" { value = aws_apigatewayv2_api.http.api_endpoint }
output "data_bucket" { value = aws_s3_bucket.data.bucket }
output "panel_table" { value = aws_dynamodb_table.panel.name }
output "console_key" {
  value     = random_password.console_key.result
  sensitive = true # read it with: terraform output -raw console_key
}
