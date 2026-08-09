# Glue Schema Registry with **backward** compatibility enforced.
#
# Three firmware generations are on the wire at once and a fourth will arrive. The registry is
# what stops generation four from being deployed with a shape the running job cannot read —
# checked at registration, before a single device is flashed, rather than discovered as a
# quarantine spike three weeks into a rollout.
#
# BACKWARD rather than FULL: a new schema must be readable by consumers built for the old one,
# which is the direction that matters when the producers are 250,000 devices that update slowly
# and the consumer is one job that updates quickly. FULL would also require old producers to
# satisfy new consumers, which forbids adding a required field for the lifetime of the oldest
# meter in the field — about fifteen years.

resource "aws_glue_registry" "meters" {
  registry_name = "${var.project}-meters"
  description   = "Payload shapes for every firmware generation in the field"
}

resource "aws_glue_schema" "meter_reading" {
  schema_name       = "meter-reading"
  registry_arn      = aws_glue_registry.meters.arn
  data_format       = "JSON"
  compatibility     = "BACKWARD"
  description       = "The union of the shapes fw1, fw2 and fw3 emit; normalisation collapses them"
  schema_definition = file("${path.module}/schemas/meter_reading.json")
}

resource "aws_glue_schema" "substation_telemetry" {
  schema_name       = "substation-telemetry"
  registry_arn      = aws_glue_registry.meters.arn
  data_format       = "JSON"
  compatibility     = "BACKWARD"
  schema_definition = file("${path.module}/schemas/substation_telemetry.json")
}
