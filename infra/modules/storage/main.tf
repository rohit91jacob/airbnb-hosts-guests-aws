# --- access logs ---------------------------------------------------------------------------

resource "aws_s3_bucket" "logs" {
  #checkov:skip=CKV_AWS_18:This is the access-log target bucket; logging it to itself would loop.
  #checkov:skip=CKV_AWS_144:Cross-region replication is not worth its cost for re-downloadable public data and logs.
  #checkov:skip=CKV2_AWS_62:No consumer needs event notifications on the log bucket.
  #checkov:skip=CKV_AWS_145:S3 server access log delivery requires SSE-S3 (AES256) on the target bucket, not SSE-KMS.
  bucket = "${var.name}-access-logs-${var.account_id}"
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

# S3 server access logging only supports SSE-S3 on the target bucket, not SSE-KMS.
resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    id     = "expire-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 365
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "logs" {
  statement {
    sid       = "S3ServerAccessLogs"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.logs.json
}


# --- data buckets --------------------------------------------------------------------------

module "lake" {
  source = "../s3_bucket"

  bucket_name   = "${var.name}-lake-${var.account_id}"
  kms_key_arn   = var.kms_key_arn
  log_bucket_id = aws_s3_bucket.logs.id
  log_prefix    = "lake"
  # Raw snapshots are immutable and re-downloadable while Inside Airbnb keeps them free
  # (about 12 months), so they move to Infrequent Access after 30 days.
  infrequent_access_prefix = "landing/"
}

module "athena_results" {
  source = "../s3_bucket"

  bucket_name     = "${var.name}-athena-results-${var.account_id}"
  kms_key_arn     = var.kms_key_arn
  log_bucket_id   = aws_s3_bucket.logs.id
  log_prefix      = "athena-results"
  noncurrent_days = 7
  expire_days     = 14
}

module "site" {
  source = "../s3_bucket"

  bucket_name   = "${var.name}-site-${var.account_id}"
  kms_key_arn   = var.kms_key_arn
  log_bucket_id = aws_s3_bucket.logs.id
  log_prefix    = "site"
}
