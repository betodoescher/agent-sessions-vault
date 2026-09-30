#!/usr/bin/env bash
# Creates (or re-applies config to) a private S3 bucket for sessions-vault. Idempotent.
# Usage: ./setup-bucket.sh [bucket-name] [region]
set -euo pipefail

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=${1:-agent-sessions-vault-$ACCOUNT}
REGION=${2:-us-east-1}

if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  if [ "$REGION" = us-east-1 ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  echo "bucket created: $BUCKET"
fi

aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'

aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled

# Old versions: keep the 5 newest for 7 days (an active store.db changes on every backup)
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{
  "Rules": [{
    "ID": "expire-old-versions",
    "Status": "Enabled",
    "Filter": {},
    "NoncurrentVersionExpiration": {"NoncurrentDays": 7, "NewerNoncurrentVersions": 5},
    "Expiration": {"ExpiredObjectDeleteMarker": true},
    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}
  }]
}' >/dev/null

aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Sid\": \"DenyInsecureTransport\",
    \"Effect\": \"Deny\",
    \"Principal\": \"*\",
    \"Action\": \"s3:*\",
    \"Resource\": [\"arn:aws:s3:::$BUCKET\", \"arn:aws:s3:::$BUCKET/*\"],
    \"Condition\": {\"Bool\": {\"aws:SecureTransport\": \"false\"}}
  }]
}"

aws s3api put-bucket-tagging --bucket "$BUCKET" \
  --tagging 'TagSet=[{Key=Project,Value=agent-sessions-vault},{Key=DataClassification,Value=Confidential}]'

echo "s3://$BUCKET"
