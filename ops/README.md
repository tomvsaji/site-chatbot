# VPS deployment hook

`deploy-site-chatbot` is installed as `/usr/local/sbin/deploy-site-chatbot`,
owned by root and not writable by the deployment account. The account's only
authorized SSH key must use a forced-command entry like:

```text
restrict,command="/usr/local/sbin/deploy-site-chatbot" ssh-ed25519 PUBLIC_KEY github-actions-site-chatbot
```

The production checkout belongs at `/srv/site-chatbot`, while state files and
the deployment lock belong at `/var/lib/site-chatbot`. The deployment account
needs access to Docker and ownership of those two directories. Production
configuration stays in `/srv/site-chatbot/.env` with mode `0600`.

GitHub's `production` environment requires these secrets:

- `VPS_HOST`
- `VPS_PORT`
- `VPS_USER`
- `VPS_DEPLOY_KEY`
- `VPS_KNOWN_HOSTS`

The private key must correspond to the restricted public key above.
