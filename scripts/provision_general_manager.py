#!/usr/bin/env python3
"""One-time, operator-run bootstrap for the NAQAA general manager account.

Run this on the live API host/container against its configured DATABASE_PATH.
Never expose this script as an HTTP endpoint and never commit credentials.
"""
import os
import re
import sys
import argparse

# Importing app uses DATABASE_PATH and initializes the existing schema.
import app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="confirm this is being run by an authorized operator")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("refusing to run without --confirm")

    email = os.getenv("NAQAA_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("NAQAA_BOOTSTRAP_ADMIN_PASSWORD", "")
    name = os.getenv("NAQAA_BOOTSTRAP_ADMIN_NAME", "مدير عام نقاء").strip()
    if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("ERROR: set NAQAA_BOOTSTRAP_ADMIN_EMAIL to the intended manager email.", file=sys.stderr)
        return 2
    if len(password) < 14 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        print("ERROR: set a unique password of at least 14 characters including letters and numbers.", file=sys.stderr)
        return 2
    if not name:
        print("ERROR: NAQAA_BOOTSTRAP_ADMIN_NAME cannot be empty.", file=sys.stderr)
        return 2

    c = app.db()
    try:
        existing = c.execute("SELECT * FROM accounts WHERE lower(email)=?", (email,)).fetchone()
        if existing:
            # Explicit operator action: promote the selected account and reset its password.
            c.execute(
                "UPDATE accounts SET role='company_director', name=?, status='active', password_hash=? WHERE id=?",
                (name, app.hp(password), existing["id"]),
            )
            account_id = existing["id"]
            action = "promoted existing account and reset password"
        else:
            account_id = str(app.uuid.uuid4())
            c.execute(
                """INSERT INTO accounts
                   (id,role,name,email,status,created_at,password_hash,account_type)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (account_id, "company_director", name, email, "active", app.now(), app.hp(password), "individual"),
            )
            wallet_id = str(app.uuid.uuid4())
            c.execute(
                """INSERT INTO wallets
                   (id,account_id,currency,balance,updated_at,balance_cents,held_cents)
                   VALUES(?,?,?,?,?,?,?)""",
                (wallet_id, account_id, "USD", 0, app.now(), 0, 0),
            )
            app.ensure_wallet_ledger(c, account_id, "USD", wallet_id)
            action = "created new general manager account"

        # Revoke all existing sessions for this account so the new password takes effect cleanly.
        c.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))
        c.commit()
        print(f"SUCCESS: {action}.")
        print(f"Manager account: {email}")
        print("Role: company_director (general manager)")
        print("Status: active")
        print("All prior sessions for this account were revoked.")
        print("Next: sign in through the app, then open company-admin.html and verify staff/listing access.")
        return 0
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
