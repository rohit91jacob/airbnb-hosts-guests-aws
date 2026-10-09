# One private data bucket with the stack's baseline: public access blocked, ACLs disabled,
# versioning, SSE-KMS with a bucket key, server access logging, lifecycle rules, TLS-only policy.

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is not worth its cost: every object can be rebuilt from the public source.
  #checkov:skip=CKV2_AWS_62:The pipeline is schedule-driven; nothing consumes S3 event notifications.
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_logging" "this" {
  bucket        = aws_s3_bucket.this.id
  target_bucket = var.log_bucket_id
  target_prefix = "${var.log_prefix}/"
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "tidy-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }

  dynamic "rule" {
    for_each = var.expire_days == null ? [] : [var.expire_days]
    content {
      id     = "expire-objects"
      status = "Enabled"
      filter {}
      expiration {
        days = rule.value
      }
      abort_incomplete_multipart_upload {
        days_after_initiation = 3
      }
    }
  }

  dynamic "rule" {
    for_each = var.infrequent_access_prefix == null ? [] : [var.infrequent_access_prefix]
    content {
      id     = "to-infrequent-access"
      status = "Enabled"
      filter {
        prefix = rule.value
      }
      transition {
        days          = 30
        storage_class = "STANDARD_IA"
      }
      abort_incomplete_multipart_upload {
        days_after_initiation = 3
      }
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]
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

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.tls_only.json
}
