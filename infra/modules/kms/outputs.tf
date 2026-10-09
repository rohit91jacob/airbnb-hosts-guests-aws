output "key_arn" {
  description = "ARN of the customer-managed key."
  value       = aws_kms_key.this.arn
}
