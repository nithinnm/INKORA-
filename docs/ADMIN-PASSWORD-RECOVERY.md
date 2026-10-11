# Existing staging admin password recovery

When the admin password is forgotten and no other administrator can issue a
recovery link, use the authenticated Google Cloud project owner in Cloud Shell:

```bash
(cd ~/INKORA- && git pull --ff-only origin main && python3 tools/cloudshell_admin_password_reset.py)
```

Enter the existing admin email and the new password twice at hidden prompts.
Use 14–256 characters and store it in a password manager. Never paste passwords,
recovery capabilities, Google sign-in codes or tokens into chat. This is a
privileged recovery operation; normal owners cannot run it through the portal.

The helper inspects the current staging image, service account, environment and
three pinned runtime secret bindings. It stops if those differ or if an earlier
recovery job already exists. A temporary secret carries the password through
stdin to Secret Manager, never CLI arguments or source files. A one-task,
no-retry job on the existing immutable image uses the existing application's
Werkzeug hashing and account models. It resets only an enabled matching admin,
increments its session version, clears lockout, invalidates recovery tokens and
records an audit event. It does not create admins, re-enable disabled accounts,
reset MFA, modify owners/kiosks/prices or alter database schema.

After success, only this helper's temporary job and secret are removed. Existing
runtime secrets and deployment remain unchanged. Sign in with the new password
and existing authenticator. Then use the admin owner-recovery action to reset an
owner's password if needed. If execution or cleanup fails, stop and inspect the
named resources; do not rerun creation/reset commands blindly. The temporary
password secret can remain on failure and needs cleanup after diagnosis.

Tests exercise password verification, session revocation, preserved MFA and
owner/kiosk state, refusal to create a missing admin, refusal to reset an owner,
and preservation of disabled-account state. Live recovery is verified only after
the user executes the job and successfully signs in.
