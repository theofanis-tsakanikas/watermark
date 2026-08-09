# A private VPC with no route to the internet, and interface endpoints for everything reached
# from inside it.
#
# No NAT gateway. Not to save the hourly charge — because egress that exists is egress somebody
# will use, and "the job can reach the internet" is the difference between a data platform and
# a data platform with an exfiltration path. Everything this estate talks to is an AWS service
# with an endpoint.
#
# The failure mode of getting the endpoint list wrong is worth knowing before it happens: a
# service with no endpoint does not refuse the connection. It hangs, until the SDK's timeout,
# and the control plane reports the application healthy throughout.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project}-vpc" }
}

# A VPC arrives with a default security group that allows all traffic between anything using it.
# Nothing in this estate does — every resource names `aws_security_group.endpoints` — but a
# default group left open is a group the next resource joins by accident, which is how a rule
# nobody wrote ends up permitting traffic nobody reviewed.
resource "aws_default_security_group" "closed" {
  vpc_id = aws_vpc.main.id
  # No ingress and no egress blocks: both empty is the point.
}

resource "aws_subnet" "private" {
  for_each = { for index, zone in var.availability_zones : zone => index }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, each.value)

  # Explicit, though it is also the default. A subnet that assigns public IPs inside a VPC with
  # no internet gateway is harmless and confusing; being able to read the intent off the
  # resource is worth one line.
  map_public_ip_on_launch = false

  tags = { Name = "${var.project}-private-${each.key}" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  # No routes. The gateway endpoints below add their own; there is deliberately no 0.0.0.0/0.
  tags = { Name = "${var.project}-private" }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

# ── Security group ───────────────────────────────────────────────────────────

#checkov:skip=CKV2_AWS_5:Attached by infra/streaming to the Managed Flink application. A layer boundary is not an unattached security group; the alternative is defining it beside the application and having the network layer own none of the network.
resource "aws_security_group" "endpoints" {
  name        = "${var.project}-endpoints"
  description = "Interface endpoints: HTTPS from inside the VPC only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from within the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "HTTPS to the endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

# ── Endpoints ────────────────────────────────────────────────────────────────

locals {
  # Every service anything in this estate calls from inside the VPC. The list is the check:
  # `scripts/check_vpc_endpoints.py` compares it against the services the Terraform actually
  # references, so a new client without an endpoint fails the build rather than hanging in
  # production.
  interface_endpoints = toset([
    "kinesis-streams",
    "kinesis-firehose",
    "logs",
    "monitoring",
    "sts",
    "kms",
    "glue",
    "athena",
    "sagemaker.api",
    "sagemaker.runtime",
    "sagemaker.featurestore-runtime",
    "states",
    "secretsmanager",
    "ecr.api",
    "ecr.dkr",
  ])
}

resource "aws_vpc_endpoint" "gateway_s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${var.project}-s3" }
}

resource "aws_vpc_endpoint" "gateway_dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${var.project}-dynamodb" }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for subnet in aws_subnet.private : subnet.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${var.project}-${each.key}" }
}

# ── Flow logs ────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/aws/vpc/${var.project}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_iam_role" "flow_logs" {
  name = "${var.project}-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "write-flow-logs"
  role = aws_iam_role.flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow_logs.arn}:*"
    }]
  })
}

resource "aws_flow_log" "main" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  iam_role_arn             = aws_iam_role.flow_logs.arn
  log_destination          = aws_cloudwatch_log_group.flow_logs.arn
  max_aggregation_interval = 60
}
