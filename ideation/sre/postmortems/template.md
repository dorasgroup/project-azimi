# Blameless Postmortem (Incident Report)

## 1. Incident Summary
- **Incident ID:** INC-XXXX
- **Date:** YYYY-MM-DD
- **Severity:** SEV-[1/2/3/4]
- **Duration:** XX Minutes
- **Incident Commander:** [Name]
- **Operations Lead:** [Name]

## 2. Impact
*Describe what the users experienced. E.g., "30% of users experienced total failure when trying to generate AI responses for 45 minutes."*

## 3. Timeline
| Time (UTC) | Action / Event | Actor |
|------------|----------------|-------|
| 10:00 | Database connection pool saturated | System |
| 10:03 | Alert: `DBPoolSaturatedFastBurn` triggered to PagerDuty | Alertmanager |
| 10:05 | Incident Declared. IC assumed command. | On-call Engineer |
| 10:25 | Mitigation applied: Scaled PgBouncer. | Operations Lead |
| 10:45 | Monitoring confirms normal error rates. Incident Resolved. | IC |

## 4. Technical Root Cause
*Why did the failure happen? Use the 5-Whys methodology. Focus entirely on the system, NOT human error.*
- Why 1: Connections exhausted.
- Why 2: A sudden spike in long-running Vector Search transactions.
- Why 3: ...

## 5. What Went Well?
- Detection was fast (<3 minutes).
- Runbook was accurate.

## 6. What Went Poorly?
- We didn't have a circuit breaker to drop traffic before saturation.

## 7. Action Items (Toil & Risk Reduction)
| Task | Type | Owner | Ticket ID | Deadline |
|------|------|-------|-----------|----------|
| Implement PgBouncer circuit breaker | Prevent Recurrence | Backend Team | JIRA-102 | +14 days |
| Update Alert thresholds | Improve Detection | SRE | JIRA-103 | +7 days |
