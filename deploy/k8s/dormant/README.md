# Dormant legacy Kubernetes manifests

These manifests were removed from `deploy/k8s/base/kustomization.yaml` when production
moved to static GitHub Pages. They are **not a production overlay** and must not be
applied to restore a public service. They remain temporarily for isolated local
migration/forensic reference only.

The directory includes the retired FastAPI public Deployment/Service/Ingress and
reader database secret example, SCOTUS analyzer, collector/publisher/retention
CronJobs, and their old network/config/alert rules. Images and sample credentials are
not maintained for production use. Prefer fixture-backed static preview and the
operator-only legacy bootstrap exporter. Delete this directory after migration and
credential revocation are independently verified.

Existing installations must explicitly delete the old resources; removing a resource
from Kustomize does not necessarily prune it safely:

```bash
kubectl -n ragchew delete ingress public service public service private-preview \
  deployment public deployment scotus-analyzer \
  cronjob scotus-discovery cronjob scotus-publisher cronjob scotus-retention \
  secret ragchew-public-db --ignore-not-found
```

Then revoke all legacy PostgreSQL/object-store/model credentials and confirm Pages is
serving the expected release ID. This command is owner/operator work and is not run by
CI.
