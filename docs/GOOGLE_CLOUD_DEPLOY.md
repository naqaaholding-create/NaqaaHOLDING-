# Google Cloud deployment for NAQAA HOLDING

This setup deploys the **existing repository** (FastAPI API and its `static/` pages) as one Cloud Run service. It does not create a demo app and does not enable real-money settlement.

## One-time Google Cloud setup

1. Open https://console.cloud.google.com/ and create/select a project for NAQAA HOLDING.
2. Link a billing account. Cloud Run / Cloud Build deployments require billing to be enabled even if usage may fit free allowances.
3. Enable Cloud Run, Cloud Build, Artifact Registry, and Resource Manager APIs.
4. Open Artifact Registry and create a **Docker** repository named `naqaa` in region `europe-west1`.
5. Open Cloud Run, choose **Connect repository**, connect GitHub, and select `naqaaholding-create/NaqaaHOLDING-` on branch `main` after these changes are merged.
6. Select Cloud Build configuration file and set its path to `cloudbuild.yaml`. Ensure the Cloud Build service account has permissions to build/push to Artifact Registry and deploy to Cloud Run, and can act as the selected runtime service account.
7. Create the trigger. After a successful build, open the Cloud Run service and copy its HTTPS URL. Test `/health`, `/api/v1/status`, and `/api/v1/production/readiness`.

## Required before production money

- This deployment deliberately sets all money/production approval flags to `0`.
- The current app uses SQLite; Cloud Run's local filesystem is not durable between instances/revisions. Do not enable live settlement until the app is migrated to a supported persistent database (for example Cloud SQL for PostgreSQL), backups and migrations are tested, and `DATABASE_PERSISTENT=1` is truthful.
- Configure `NAQAA_ADMIN_KEY`, Reown project settings, email provider secrets, and any provider/webhook secrets in Secret Manager or Cloud Run secrets, never in Git.
- Keep SafePal/Reown as the external wallet connection; server-side transaction verification and replay/amount/network checks must be implemented and tested.
- Complete KYC/KYB, legal/compliance approval, monitoring, and the financial regression suite before changing any production approval flag.
- Public unauthenticated access is enabled for the API so the public app can reach its endpoints. Every private/admin/financial route must enforce its own authentication and authorization.

## Notes

- Firebase Hosting is not required to serve this existing app: the FastAPI service serves the pages and API together. Firebase can be added later if you want a separate static frontend or Firebase Authentication.
- Google Cloud is not automatically free. Review budgets and billing alerts before deploying.
