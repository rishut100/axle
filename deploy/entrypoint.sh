#!/bin/bash

echo "Initializing application for Region: $AWS_REGION"

source /app/deploy/config/$AWS_REGION/application.conf

if [[ -z $AWS_REGION ]]; then
	echo "AWS_REGION field is mandatory. Exiting without it."
	exit 1
fi

echo "Getting GCP credentials from secret: $GCP_SECRET_NAME"
gcp_creds=$(aws secretsmanager get-secret-value --region $AWS_REGION --secret-id $GCP_SECRET_NAME --query SecretString --output text)
echo "Getting Drive credentials from secret: $DRIVE_CREDS_NAME"
drive_creds=$(aws secretsmanager get-secret-value --region $AWS_REGION --secret-id $DRIVE_CREDS_NAME --query SecretString --output text)


printf '%s\n' "$gcp_creds" > $GOOGLE_APPLICATION_CREDENTIALS

export GIT_TOKEN=$(jq -r '."git.token"' <<< "$drive_creds")
export DB_PASS=$(jq -r '."spring.datasource.password"' <<< "$drive_creds")
export SENDGRID_API_KEY=$(jq -r '."sendgrid.api.key"' <<< "$drive_creds")
export OKTA_API_TOKEN=$(jq -r '."okta.api-token"' <<< "$drive_creds")
export TWILIO_AUTH_TOKEN=$(jq -r '."twilio.auth.token"' <<< "$drive_creds")
export TWILIO_ACCOUNT_SID=$(jq -r '."twilio.account.sid"' <<< "$drive_creds")
export SLACK_BOT_TOKEN=$(jq -r '."axle.tenant.access.slack.bot.token"' <<< "$drive_creds")
export SLACK_SIGNING_SECRET=$(jq -r '."axle.tenant.access.slack.secret"' <<< "$drive_creds")
export EU_AXLE_TOKEN=$(jq -r '."eu.axle.token"' <<< "$drive_creds")

# Garage/Kickstart secrets (axle-prod-secrets) — keys are already env-var-named; export each verbatim.
echo "Getting Garage/Kickstart secrets from secret: $AXLE_SECRETS_NAME"
axle_creds=$(aws secretsmanager get-secret-value --region $AWS_REGION --secret-id $AXLE_SECRETS_NAME --query SecretString --output text)
while IFS= read -r kv; do export "$kv"; done < <(jq -r 'to_entries[] | "\(.key)=\(.value)"' <<< "$axle_creds")

# https://github.com/Yelp/dumb-init
/usr/local/bin/dumb-init -- python wsgi.py
