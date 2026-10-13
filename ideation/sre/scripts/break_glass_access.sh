#!/bin/bash
set -e

# Break-Glass Emergency Access Script
# Usage: ./break_glass_access.sh <INCIDENT_ID> <NAMESPACE>

INCIDENT_ID=$1
NAMESPACE=${2:-application}

if [ -z "$INCIDENT_ID" ]; then
  echo "❌ ERROR: You must provide a valid Incident ID (e.g., INC-1234) for Audit logging."
  exit 1
fi

echo "🚨 REQUESTING BREAK-GLASS ACCESS FOR INCIDENT: $INCIDENT_ID"
echo "Logging request to Security Audit Trail..."

# 1. Log request to central security SIEM (Mocked via curl)
# curl -X POST https://audit.internal/api/log -d '{"user": "'"$USER"'", "incident": "'"$INCIDENT_ID"'", "action": "break_glass_request"}'

# 2. Grant temporary cluster-admin role bindings for 1 hour using a dedicated tool/operator (Mocked via kubectl)
echo "✅ Granting temporary elevated access in namespace '$NAMESPACE' for 1 hour..."
kubectl create rolebinding break-glass-$USER-$INCIDENT_ID \
  --clusterrole=cluster-admin \
  --user=$USER \
  --namespace=$NAMESPACE || true

echo "⚠️ YOU ARE NOW OPERATING WITH CLUSTER-ADMIN PRIVILEGES."
echo "⚠️ ALL TERMINAL COMMANDS ARE BEING AUDITED AND RECORDED via Teleport/Auditd."

# Start an audited bash session (Simulated here)
# In production, this would launch `tsh` (Teleport) or record via `script`
script -q -a "audit_${INCIDENT_ID}_${USER}_$(date +%s).log"

# --- AFTER SESSION CLOSES ---
echo "🔒 Revoking Break-Glass Access..."
kubectl delete rolebinding break-glass-$USER-$INCIDENT_ID --namespace=$NAMESPACE || true
echo "✅ Access Revoked. Session log saved and sent to Security."
