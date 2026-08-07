# Deploy — PHATT-RAID (Unraid)

Live Obsessed container runs on PHATT-RAID at `10.0.0.100:10198`. This runbook covers two equivalent redeploy paths and a health probe.

## Image naming

`docker-compose.yml` (after PHA-1342) derives the image from `DOCKERHUB_USER` with a safe fallback:

```
image: ${DOCKERHUB_USER:-therealphatt}/obsessed:latest
```

CI (`/.github/workflows/build.yml`) pushes to `${{ secrets.DOCKERHUB_USER }}/obsessed:latest`. Today both resolve to `therealphatt/obsessed:latest`. Keep the env var in sync with `DOCKERHUB_USER` on PHATT-RAID.

## Path A — Unraid UI (one click)

In Unraid Docker tab → click the `obsessed` container → **Force Update**. Image refreshes, container recreates, port `10198` re-binds.

## Path B — SSH + script (reproducible)

From a workstation:

```bash
ssh root@10.0.0.100
sudo bash /mnt/user/appdata/obsessed/scripts/host-update.sh
```

The script does `docker compose pull` → `up -d` → polls `http://127.0.0.1:10198/api/health` for up to 60s. Logs land in `/var/log/obsessed/host-update.log`.

Exit codes:

- `0` healthy
- `2` image pull failed
- `3` recreate failed
- `4` container up but `/api/health` never returned 200

## Health probe (manual)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://10.0.0.100:10198/api/health
# expect: 200
```

## Backout

If a freshly-pulled image misbehaves:

```bash
ssh root@10.0.0.100
cd /mnt/user/appdata/obsessed
docker compose down
docker tag therealphatt/obsessed:<last-good-sha> therealphatt/obsessed:latest
docker compose up -d
```

Pin a known-good SHA via `IMAGE_NAME` in `build.yml` and the matching compose `image:` tag before pulling.

## Related issues

- **PHA-1342** — image-name drift fix (this runbook)
- **PHA-170**  — Obsessed CI/CD chain (DOCKERHUB_USER / DOCKERHUB_TOKEN secrets)
