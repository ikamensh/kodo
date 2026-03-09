#!/usr/bin/env bash
# Pull Cloud Run logs for the kodo-bench service.
#
# Usage:
#   ./benchmark/online/logs.sh              # last 50 entries
#   ./benchmark/online/logs.sh 100          # last 100 entries
#   ./benchmark/online/logs.sh 20 errors    # last 20 error entries
#   ./benchmark/online/logs.sh 50 requests  # last 50 HTTP request logs

set -euo pipefail

PROJECT="covenance-469421"
SERVICE="kodo-bench"
REGION="europe-west1"
LIMIT="${1:-50}"
MODE="${2:-all}"

case "$MODE" in
  errors)
    echo "=== Errors (last $LIMIT) ==="
    gcloud logging read \
      "resource.type=cloud_run_revision AND resource.labels.service_name=$SERVICE AND severity>=ERROR" \
      --project="$PROJECT" --limit="$LIMIT" \
      --format="table(timestamp,httpRequest.status,httpRequest.requestUrl,textPayload)" \
      2>&1
    ;;
  requests)
    echo "=== HTTP Requests (last $LIMIT) ==="
    gcloud logging read \
      "resource.type=cloud_run_revision AND resource.labels.service_name=$SERVICE AND httpRequest.requestUrl:*" \
      --project="$PROJECT" --limit="$LIMIT" \
      --format="table(timestamp,httpRequest.status,httpRequest.latency,httpRequest.requestMethod,httpRequest.requestUrl,httpRequest.responseSize)" \
      2>&1
    ;;
  startup)
    echo "=== Startup / Instance logs (last $LIMIT) ==="
    gcloud logging read \
      "resource.type=cloud_run_revision AND resource.labels.service_name=$SERVICE AND textPayload:*" \
      --project="$PROJECT" --limit="$LIMIT" \
      --format="table(timestamp,textPayload)" \
      2>&1
    ;;
  *)
    echo "=== All logs (last $LIMIT) ==="
    gcloud logging read \
      "resource.type=cloud_run_revision AND resource.labels.service_name=$SERVICE" \
      --project="$PROJECT" --limit="$LIMIT" \
      --format="table(timestamp,severity,httpRequest.status,httpRequest.requestMethod,httpRequest.requestUrl,textPayload)" \
      2>&1
    ;;
esac
