# Provision the NAQAA general manager account

This is a controlled, one-time operator action. It does **not** create an unauthenticated web endpoint and it does not enable real-money settlement.

## Before running

1. Confirm the production API service and its configured `DATABASE_PATH`. Do not run against a local or temporary database if the goal is to log into the live app.
2. Make a database backup and confirm it can be restored.
3. Use the service's authenticated shell/one-off command facility. Do not put credentials in GitHub files, commit messages, screenshots, or chat.
4. Set these environment variables in that shell session (use a unique password of at least 14 characters):
   - `NAQAA_BOOTSTRAP_ADMIN_EMAIL`: the email address you will use to sign in.
   - `NAQAA_BOOTSTRAP_ADMIN_PASSWORD`: a new unique password.
   - `NAQAA_BOOTSTRAP_ADMIN_NAME`: optional display name; defaults to `مدير عام نقاء`.
5. Run from the repository root:
   ```sh
   python scripts/provision_general_manager.py --confirm
   ```

The script creates the account if it does not exist. If that email already exists, it promotes that exact account to `company_director`, activates it, resets its password, and revokes its previous sessions. Therefore, double-check the email before running.

## Verify after provisioning

1. Open the **live app URL** and sign in with the chosen email/password.
2. Confirm the account profile reports role `company_director`.
3. Open `company-admin.html`; verify the company profile, four departments, employee list and job applications load.
4. Open the listing/admin screens and verify the API returns authorized results.
5. Sign out and sign back in to confirm the new session works.

If login succeeds but the dashboard shows a company setup/API error, stop and inspect the live API logs and database. Do not bypass role checks or use a client-side role flag as proof of authorization.

## Important limits

- This repository change only adds the safe provisioning procedure. It does not execute it on the production host.
- A GitHub commit cannot prove which service URL, database, or deployment is live.
- Keep `REAL_MONEY_ENABLED=0`; manager access does not authorize real-money collection. Payment-provider verification and production gates remain separate.
