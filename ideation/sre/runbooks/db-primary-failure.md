# Runbook: DB Primary Failure (PostgreSQL)

**Service:** PostgreSQL  
**Tier:** Tier-0 (Critical Infrastructure)  
**Severity:** SEV-1  
**Owner:** Infrastructure Team / DBA  
**Last Reviewed:** 2026-07-20  

## 🚨 Symptoms & Impact
- Core API throws `500 Internal Server Error` (Database Connection Refused/Timeout).
- Ingestion Workers are stuck in `CrashLoopBackOff` or throwing backlogs.
- Total application outage for write operations (Upload, Chat saving).

## 🛠 Preconditions
- Access to ArgoCD.
- Access to Grafana (PostgreSQL Dashboard).
- Active Break-Glass credentials if manual DB intervention is needed.

## 🕵️ Triage Steps
1. **Check Grafana:** Go to `Dashboards -> PostgreSQL HA`. Check if the Primary node is marked as `Down`.
2. **Check Logs:** Run `kubectl logs -l app=postgresql -n infrastructure --tail=100` to look for OOMKills, Disk Full errors, or Network Partition.
3. **Verify Patroni/HA Leader Election:** `kubectl exec -it pg-cluster-0 -n infrastructure -- patronictl list`

## 🛡 Safe Mitigation (Operations Lead)
If Patroni (HA Manager) failed to auto-failover:
1. Trigger manual failover gracefully:
   ```bash
   patronictl failover --candidate pg-cluster-1
   ```
2. Verify connections are routing to the new Primary via PgBouncer.

## 🛑 Dangerous Actions (Requires IC Approval)
- **DO NOT** restart the entire StatefulSet manually. This can cause split-brain if not isolated.
- **DO NOT** clear WAL logs manually. This breaks Replication and Point-in-Time Recovery (PITR).

## 🔄 Recovery & Verification
- Verify Core API Healthcheck is `200 OK`.
- Run a dummy upload test on the frontend.
- Announce mitigation in `#incidents-critical` and update the Status Page.
