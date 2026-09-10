I have enough evidence. Here is the review.

---

# Security review — jayserver (192.168.0.48), 2026-09-07

## Overall posture

This box is in noticeably better shape than a typical small-business server: UFW is active with a default-deny inbound policy and is demonstrably dropping LAN scans, unattended-upgrades is running with zero packages upgradable, journald is persistent, Postgres publishes no host port, the secrets directory is `0700`, and no secret value appears in container logs, shell history, the journal, or git. The dominant risk is not a missing control — it's that the perimeter has collapsed into a single trust tier: **SSH password authentication is live** (a `00-jay.conf` drop-in written at 07:12 today silently overrides the `01-hardening.conf` written at 01:07), there is no fail2ban, UFW allows *everything* on `tailscale0`, and Tailscale SSH is enabled — so any LAN host with jayserver's password, or any device on the tailnet, lands on an account that is in the `docker` group and can read `gmail-domain.json` directly. Below that, the crown-jewel service-account key sits unencrypted on an unencrypted NVMe, is bind-mounted into four containers that run as uid 1000 with no capability drops, and is duplicated into a restic repository on the same disk whose only snapshot is an incomplete one that reported failure. Backups are not actually scheduled — the timer units exist on disk but are not installed — so there is currently no working backup and no alerting if the host dies.

Worst realistic outcome, reachable in one step from several places: read of `~/jay-os/secrets/gmail-domain.json`, which is domain-wide delegation over every company mailbox.

---

## Critical

### C1. SSH password authentication is enabled and actively used; no fail2ban, no rate limiting

**What.** `/etc/ssh/sshd_config` does `Include /etc/ssh/sshd_config.d/*.conf`. OpenSSH takes the **first** value obtained for a keyword, and the drop-ins are read in glob order, so `00-jay.conf` wins over `01-hardening.conf`.

```
$ ls -la /etc/ssh/sshd_config.d/ ; cat 00-jay.conf 01-hardening.conf
-rw-r--r-- 1 root root  27 Sep  7 07:12 00-jay.conf
-rw-r--r-- 1 root root 102 Sep  7 01:07 01-hardening.conf
## 00-jay.conf
PasswordAuthentication yes          <-- wins (read first)
## 01-hardening.conf
PasswordAuthentication no           <-- dead
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
```

Confirmed in use, after the hardening drop-in was written:

```
$ grep -hoE 'Accepted (password|publickey) for [a-z]+ from [0-9.]+' /var/log/auth.log | sort | uniq -c
    238 Accepted publickey for jayserver from 192.168.0.66
     12 Accepted password for jayserver from 192.168.0.66
      3 Accepted password for jayserver from 192.168.0.48
      1 Accepted password for jayserver from 100.85.8.63     <-- over the tailnet
$ grep 'Accepted password' /var/log/auth.log | tail -1
2026-09-07T22:53:18 ... 192.168.0.66
```

`sshd` listens on `0.0.0.0:22` and `[::]:22`; UFW allows `22/tcp` from anywhere. `fail2ban` is `inactive` / `not-found`, `/etc/fail2ban` does not exist.

**Why it matters here.** Password guessing against port 22 from the LAN or Wi-Fi is unthrottled and unmonitored. One success is a full compromise: jayserver is in `docker` (root-equivalent) and owns `~/jay-os/secrets/gmail-domain.json`. The pubkey path already works (238 successful key logins from 192.168.0.66), so passwords buy nothing.

**Fix (needs root).** Remove the override rather than editing `01-hardening.conf`, so intent stays in one file:

```bash
sudo rm /etc/ssh/sshd_config.d/00-jay.conf
sudo sshd -t && sudo systemctl reload ssh
```

Verify with `sudo sshd -T | grep -i passwordauth` → `passwordauthentication no`.

**Disruption.** Does not touch Email Sentinel or the Claude service. Keep your current session open while you do it. Two out-of-band recovery paths exist if you lock yourself out: the physical console (`tty1` is logged in) and Tailscale SSH (see H2), which does not use sshd's auth path. `/etc/ssh/sshd_config.d/50-cloud-init.conf` is `0600 root:root` and unreadable to me — check it doesn't re-enable passwords before you rely on this.

---

### C2. The Google Workspace domain-wide-delegation key is reachable by too many principals at once

**What.** The file itself is stored correctly:

```
$ ls -la ~/jay-os/secrets/
drwx------ 2 jayserver jayserver 4096 .
-rw------- 1 jayserver jayserver 2386 gmail-domain.json
-rw------- 1 jayserver jayserver  645 gmail-info.json
$ python3 -c "...keys only..."
gmail-domain.json: ['auth_provider_x509_cert_url','auth_uri','client_email','client_id',
 'client_x509_cert_url','private_key','private_key_id','project_id','token_uri','type','universe_domain']
type= service_account   client_email domain= pcd-email-tracker.iam.gserviceaccount.com
gmail-info.json:   ['client_id','client_secret','refresh_token','scopes','token_uri']
```

But the set of things that can read it is large. Each of these is one step from the plaintext key, with no further authentication:

| Principal | Path to the key |
|---|---|
| Anyone who authenticates as `jayserver` over SSH | direct read (C1) |
| Any device on the tailnet | Tailscale SSH + `ufw allow in on tailscale0` (H2) |
| Any code in the 4 sentinel containers | `/opt/jay-os/secrets:ro` bind mount, running as uid 1000 (H4) |
| Anything that reaches Dockge on the tailnet | `docker.sock` → root → everything (H1) |
| Anyone holding the NVMe | no disk encryption (H5) |
| The restic repo on the same disk | `restic backup ... /home/jayserver/jay-os` includes `secrets/` (H6) |
| The `claude remote-control` process | runs as jayserver with `--permission-mode auto` (H3) |

**Why it matters here.** This is the stated worst outcome. A service-account key with domain-wide delegation is not revocable by changing a password — it reads every mailbox in the Workspace until the key is deleted in GCP and the delegation removed in Admin Console.

**Fix.** No single command. Reduce the principal list by fixing C1, H1, H2, H3, H4 below. Independently, in Google Cloud Console, scope the delegation as tightly as Gmail allows (`gmail.readonly` only, and restrict the client ID's OAuth scopes in Admin Console → Security → API controls → Domain-wide delegation), and set a key-rotation reminder. Consider whether the four enabled mailboxes (`info@`, `jay@`, `eric@`, `projects@`, `guillermo@`) justify domain-wide delegation at all versus per-mailbox OAuth like `gmail-info.json`.

**Disruption.** Rotating the key requires replacing `secrets/gmail-domain.json` and restarting the sentinel containers — capture is checkpointed in Postgres, so a restart loses nothing.

---

## High

### H1. Dockge holds a read-write Docker socket and is exposed on the tailnet

```
$ docker inspect dockge
user="" priv=false netmode=dockge_default
PORTS: {"5001/tcp":[{"HostIp":"100.85.8.63","HostPort":"5001"}]}
MOUNTS: [bind /srv/stack/dockge/data -> /app/data rw=true]
        [bind /srv/stack -> /srv/stack rw=true]
        [bind /var/run/docker.sock -> /var/run/docker.sock rw=true]
$ ls -la /run/docker.sock
srw-rw---- 1 root docker 0 /run/docker.sock      # not rootless; daemon runs as root
$ docker images | grep dockge
louislam/dockge:1   335c6368b880   17 months ago
```

**Why it matters here.** A writable `docker.sock` is unrestricted root on the host — `docker run -v /:/host --privileged` is available to anything that reaches it. The container also has `/srv/stack` read-write, which contains `n8n/.env`, `nextcloud/.env`, `maintenance/.restic-pass` and `maintenance/.restic-env`. The image is 17 months old and receives no updates from `unattended-upgrades`. Its only access control is the tailnet bind plus Dockge's own login form — and UFW permits everything on `tailscale0`, so every tailnet device can reach it.

**Fix.** In order of preference:

1. Stop running it continuously: `docker stop dockge` when not in use (does not affect other stacks).
2. If you keep it, put a socket proxy in front so it cannot create privileged containers, e.g. add a `tecnativa/docker-socket-proxy` service with `POST=0` and mount *that* into Dockge instead of `/var/run/docker.sock`.
3. Update the image: `docker compose -f /srv/stack/dockge/compose.yaml pull && ... up -d`.
4. Drop the `/srv/stack:/srv/stack` mount if you accept losing Dockge's stack editing, or at minimum relocate `.restic-pass`/`.restic-env` out of `/srv/stack` (see H6).

**Disruption.** Restarting or stopping Dockge does not touch Email Sentinel, n8n, Nextcloud or the Claude service.

### H2. The tailnet is a single flat trust zone: Tailscale SSH on, plus `allow in on tailscale0`

```
$ tailscale debug prefs
"RunSSH": true,          <-- Tailscale SSH server enabled
"ShieldsUp": false,
"AdvertiseTags": null,   <-- node is untagged; ACL is whatever the tailnet default is
$ tailscale status
100.85.8.63    jayserver   jay@  linux
100.113.47.91  iphone181   jay@  iOS
100.68.27.21   moneymaker  jay@  windows
$ grep -A6 'To  ' ~/harden-d.log     # UFW ruleset as applied 01:07 today
22/tcp                     ALLOW IN    Anywhere                   # SSH
Anywhere on tailscale0     ALLOW IN    Anywhere                   # tailnet
```

**Why it matters here.** With `RunSSH: true` and a default-permissive tailnet ACL, the iPhone and the Windows desktop can SSH in *without* an SSH key and without touching sshd — fixing C1 does not close this path. Separately, `allow in on tailscale0` means those devices also reach AdGuard's admin UI on `:3000`, n8n on `:5678`, Dockge on `:5001`, and DNS on `:53`. A stolen or compromised phone is equivalent to root on this server.

**Fix.** In the Tailscale admin console (no root needed on this host):
- Write an explicit ACL rather than relying on the default `"*":"*"`. Restrict `tag:server` port 22 and the service ports to specific devices.
- Add an SSH policy that requires `checkPeriod` re-authentication, or disable Tailscale SSH here entirely: `sudo tailscale set --ssh=false` (**needs root**).
- Enable device approval and key expiry (`KeyExpiry 2027-03-06`) for the tailnet.
- On the host, replace the blanket `ufw allow in on tailscale0` with per-port rules (**needs root**):
  ```bash
  sudo ufw delete allow in on tailscale0
  sudo ufw allow in on tailscale0 to any port 22 proto tcp
  sudo ufw allow in on tailscale0 to any port 53
  sudo ufw allow in on tailscale0 to any port 3000 proto tcp
  ```
  Note that Dockge's and n8n's published ports are DNAT'd and bypass UFW's INPUT chain regardless, so their `100.85.8.63` bind is the real control there.

**Disruption.** Disabling Tailscale SSH does not affect sshd, containers, or the Claude service. Do not narrow ACLs and disable password auth in the same sitting without console access.

### H3. The Claude remote-control session runs unattended as jayserver with `--permission-mode auto` and no permission policy

```
$ cat ~/.config/systemd/user/claude-remote.service
ExecStart=%h/.local/bin/claude remote-control --name jayserver --permission-mode auto
Restart=always
$ ls -la /var/lib/systemd/linger/     # linger enabled -> survives logout, runs with no session
-rw-r--r-- 1 root root 0 Sep  7 22:49 jayserver
$ python3 -c "...settings.json..."
has permissions key: False
hooks: False
$ ls /home/jayserver/jay-os/.claude ; ls ~/.claude/CLAUDE.md
No such file or directory (neither exists)
```

**Why it matters here.** This process has jayserver's full authority — the `docker` group, `~/jay-os/secrets`, `~/.ssh`, `/srv/stack` — with auto-approval and no `permissions` allowlist/denylist, no hooks, and no project `CLAUDE.md` constraining it. `~/.claude/.credentials.json` (0600) and `~/.claude.json` (0600, contains `oauthAccount`) are correctly permissioned, but a session driven by a prompt from anywhere can read the crown-jewel key without prompting — which is exactly what this review just did.

**Fix (no root).** Add a deny rule for the secret paths in `~/.claude/settings.json`, e.g.

```json
"permissions": {
  "deny": [
    "Read(/home/jayserver/jay-os/secrets/**)",
    "Read(/home/jayserver/.ssh/id_*)",
    "Read(/srv/stack/**/.env)",
    "Read(/srv/stack/maintenance/.restic-*)"
  ]
}
```

and consider dropping `--permission-mode auto` for a review-gated mode. **I did not modify the unit or the process, per your instruction** — the unit edit and `systemctl --user restart claude-remote` are yours to make. Note `settings.json` is mode `664` and `~/.claude` is `775`; tighten to `600`/`700` while you are there.

### H4. Sentinel containers run as uid 1000 with all default capabilities and the secrets mounted in

```
$ docker inspect email-sentinel-email-ingest-1
user="1000:1000" priv=false caps_add=[] caps_drop=[] secopt=[] ro_rootfs=false
MOUNTS: [bind .../config -> /app/config rw=false]
        [bind .../data   -> /app/data   rw=true]
        [bind /home/jayserver/jay-os/secrets -> /opt/jay-os/secrets rw=false]
$ docker inspect ... | sed -E 's/=(.*)/=<redacted>/'
SENTINEL_DSN=<redacted>   EMAIL_PASSWORD=<redacted>   POSTGRES_PASSWORD=<redacted>
NOTION_API_KEY=<redacted> JAYOS_NOTION_BRIDGE_SECRET=<redacted>
```

The compose file defaults to uid 10001 and documents the trade-off honestly, but `.env` sets `SENTINEL_UID`/`SENTINEL_GID`, and the running containers are `1000:1000`.

**Why it matters here.** Your threat model explicitly includes a malicious dependency. Inside these containers, such a dependency reads `gmail-domain.json` and the SMTP app password from the environment as a first-class capability — that part is inherent to the design. What is *avoidable* is everything on top: no `cap_drop: ALL`, no `no-new-privileges`, a writable root filesystem, and a uid that matches the host owner of `/app/data` (which is mode 777, so container-side writes land as jayserver-owned files). The mounts are correctly `ro` for `config` and `secrets`, and Postgres correctly publishes no port (`connect to 127.0.0.1:5432` → Connection refused).

**Fix.** In `apps/email-sentinel/deploy/docker-compose.yml`, under the `x-sentinel` anchor:

```yaml
  cap_drop: [ALL]
  security_opt:
    - no-new-privileges:true
  read_only: true
  tmpfs:
    - /tmp
```

Then `docker compose -f apps/email-sentinel/deploy/docker-compose.yml up -d`. Longer term, prefer `secrets:` over `env_file:` for `EMAIL_PASSWORD` and `POSTGRES_PASSWORD` so they are files rather than inspectable environment (note: `SENTINEL_DSN` is interpolated with the password at compose-render time, so it will remain visible in `docker inspect` until that is restructured to read the password from a file).

**Disruption.** This restarts Email Sentinel (all four workers plus Postgres stay up; only the workers recreate). State lives in Postgres and ingest is checkpointed, so a restart loses no mail. Test `read_only: true` first — the image writes nothing I could see, but a failed start would pause ingestion.

### H5. No disk encryption on a physically accessible desktop-class machine

```
$ lsblk -o NAME,SIZE,FSTYPE,TYPE,MOUNTPOINT
nvme0n1     931.5G          disk
├─nvme0n1p1     1G vfat     part /boot/efi
└─nvme0n1p2 930.5G ext4     part /          <-- no crypto_LUKS anywhere
$ swapon --show
/swap.img  file  8G                          <-- unencrypted swap
$ hostnamectl
Hardware Model: EliteMini Series             <-- a mini PC, trivially pocketable
```

**Why it matters here.** Anyone who walks off with this box — or just pulls the NVMe — has `gmail-domain.json`, the SMTP app password, the GitHub deploy key, `~/.claude/.credentials.json`, the restic repo, and Nextcloud's data, with no work required.

**Fix.** Full-disk encryption cannot be added in place; it requires a reinstall with LUKS (and, on a headless box, either a passphrase at every boot or Clevis/TPM unlock). That is a project, not a command. The cheap interim mitigations: physically secure the unit, and treat the key as compromised if the hardware ever leaves your control. **Needs root and a reinstall.**

### H6. Backups are not scheduled, the only snapshot failed, and the repo is on the same disk as the data

```
$ systemctl list-timers --all | grep -i jayserver
NOT in system timers
$ systemctl --user list-timers --all | grep -i jayserver
NOT in user timers
$ ls /srv/stack/maintenance/systemd/
jayserver-backup.service  jayserver-backup.timer  jayserver-health.service  jayserver-health.timer
   # written, never installed into /etc/systemd/system
$ cat /srv/stack/maintenance/reports/backup-latest.json
"ok": false, "errors": ["restic backup exited 3"],
"snapshot": {"username":"jayserver","uid":1000, "paths":["/home/jayserver/jay-os","/srv/data","/srv/stack"]}
$ ls /home/jayserver/backup/restic/snapshots/   # exactly one snapshot, from 20:09 today
$ du -sh /home/jayserver/backup/restic          # 325M — on /dev/nvme0n1p2, the only disk
```

**Why it matters here.** Four separate problems compound:
1. **No backups are running.** The timers exist as files but were never installed, so nothing runs nightly.
2. **The one snapshot is incomplete and known-bad.** It was taken as uid 1000, not root — exactly the failure mode `backup.sh` was written to refuse (`REFUSING TO RUN: backup.sh must run as root`), meaning `/srv/data/nextcloud` (www-data) and AdGuard's `0600 root:root` config were silently skipped. `restic` exited 3 (unreadable files) and the report says `ok: false`.
3. **The repo is on the same disk it backs up.** Drive failure or ransomware takes both.
4. **The repo contains the crown jewel, and its password lives inside the backup set.** `restic backup ... /home/jayserver/jay-os` has no exclude for `secrets/` or `deploy/.env`, and `/srv/stack/maintenance/.restic-pass` is itself inside the `/srv/stack` path being snapshotted.

Also: nothing alerts if the host dies. `health-report.sh` writes JSON for "Hermes" to consume, but contains no mail/webhook call (`grep -iE 'mail|smtp|curl|webhook|notify'` finds only a local `curl` health probe of n8n), and its timer is not installed either.

**Fix (needs root for the install).**
```bash
sudo cp /srv/stack/maintenance/systemd/jayserver-{backup,health}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jayserver-backup.timer jayserver-health.timer
sudo /srv/stack/maintenance/backup.sh     # first correct, root-owned run
```
Before that first run, make three changes to `backup.sh`/config (no root needed to edit):
- Point `RESTIC_REPOSITORY` in `.restic-env` at off-box storage (a USB disk, a second machine, or a B2/S3 bucket with append-only credentials).
- Move `.restic-pass` and `.restic-env` out of `/srv/stack` — e.g. `/root/.restic/` mode 600 — so the repo key is not inside the repo.
- Decide deliberately whether `~/jay-os/secrets` belongs in the backup. If yes, the repo *must* be off-box and its password stored separately; if no, add `--exclude /home/jayserver/jay-os/secrets`.
- Add alerting: the simplest reliable option here is a healthchecks.io-style dead-man ping from `jayserver-backup.service` (`ExecStopPost=curl -fsS -m 10 <url>/$EXIT_STATUS`), since a server that is down cannot send its own alert.

**Disruption.** `backup.sh` puts Nextcloud into maintenance mode for the duration of the file snapshot (it has a trap to restore it). It does not touch Email Sentinel or the Claude service.

---

## Medium

### M1. `jayserver` is in `docker` *and* `sudo` *and* `lxd` — the `docker` membership makes the password prompt decorative

```
$ id
uid=1000(jayserver) groups=...,27(sudo),101(lxd),988(docker)
$ sudo -n true 2>&1
sudo: a password is required            # good: no passwordless sudo
$ ls -la /var/snap/lxd/common/lxd/unix.socket
srw-rw---- 1 root lxd 0                 # jayserver can reach it...
$ systemctl is-active snap.lxd.daemon
inactive                                # ...but the daemon is not running
```

**Why it matters.** Requiring a sudo password is worth little when the same account can run `docker run -v /:/host --privileged alpine chroot /host` with no prompt. Every finding above that ends at "an attacker is jayserver" therefore ends at root.

**Fix.** Accept it as an explicit decision (you administer Docker from this account), or remove `jayserver` from `docker` and prefix container work with `sudo` (**needs root**: `sudo gpasswd -d jayserver docker`). *Warning: this would break the Claude service's and your own ability to run `docker compose` without sudo, and Dockge would be unaffected since it uses the socket directly.* At minimum, remove the unused `lxd` membership: `sudo gpasswd -d jayserver lxd`, and consider `sudo snap remove lxd` since the daemon is inactive.

### M2. Nextcloud is published on the LAN over plain HTTP, and Docker's published ports bypass UFW

```
$ docker inspect nextcloud
PORTS: {"80/tcp":[{"HostIp":"192.168.0.48","HostPort":"8080"},{"HostIp":"100.85.8.63","HostPort":"8080"}]}
$ curl -s -o /dev/null -w '%{http_code}' http://192.168.0.48:8080/     # 302 (from the host)
```
Published container ports are DNAT'd in `nat/PREROUTING` and traverse `FORWARD`, so UFW's INPUT rules do not apply to them. Corroborating evidence: while 192.168.0.66 was port-scanning this host at 23:21, UFW logged blocks for `DPT=3000`, `5678`, `5001`, `37500` and `5432`, but every `DPT=8080` block in the journal comes from a *container* subnet (`SRC=172.18.0.4`, `IN=br-...`), never from a LAN address.

**Why it matters here.** Nextcloud login credentials and file contents cross the LAN/Wi-Fi in cleartext, and the LAN is in your threat model. This is also the one service whose exposure UFW cannot fix.

**Fix.** Either drop the LAN binding and reach Nextcloud over the tailnet only —
```yaml
    ports:
      - "100.85.8.63:8080:80"     # remove the 192.168.0.48 line
```
`docker compose -f /srv/stack/nextcloud/docker-compose.yml up -d` — or put a TLS reverse proxy (Caddy) in front and publish only 443. **Disruption:** restarts Nextcloud only. Note the compose comment in `dockge/compose.yaml` already states this rule correctly ("never 0.0.0.0 on this box") — Nextcloud is the one stack that departs from it.

### M3. n8n runs with `N8N_SECURE_COOKIE=false` over plain HTTP and holds third-party credentials

```
$ docker inspect n8n | sed -E 's/=(.*)/=<redacted>/'
N8N_ENCRYPTION_KEY=<redacted>  DB_POSTGRESDB_PASSWORD=<redacted>  N8N_SECURE_COOKIE=<redacted>
PORTS: {"5678/tcp":[{"HostIp":"127.0.0.1",...},{"HostIp":"100.85.8.63",...}]}
$ grep N8N_SECURE_COOKIE /srv/stack/n8n/compose.yaml
      N8N_SECURE_COOKIE: "false"
```

**Why it matters here.** The compose comments say n8n's stored credentials cover PandaDoc, PayPal, Fillout and Roam, and `sentinel.toml` notes n8n sends as `support@professionalcadesign.com`. Session cookies cross the tailnet without the `Secure` flag. The tailnet binding is the only access control, and per H2 that means every tailnet device. Positives: it is version-pinned (`2.37.7`), runs as `node` (non-root), and its DB publishes no port.

**Fix.** Complete the "phase 07" HTTPS front-end the compose file already anticipates, then set `N8N_SECURE_COOKIE: "true"`. Until then, ensure n8n's own owner account uses a strong unique password and enable its MFA. **Disruption:** restarts n8n only.

### M4. World-writable files and directories throughout the application tree

```
$ find ~/jay-os -perm -o+w -not -path '*/.git/*' | wc -l
82
$ find ~/jay-os -perm -o+w -printf '%M %p\n' | head -4
drwxrwxrwx  .../orchestrator/permit_pipeline
-rwxrwxrwx  .../orchestrator/permit_pipeline/classify.py
-rwxrwxrwx  .../apps/email-sentinel/deploy/docker-compose.yml
-rwxrwxrwx  .../apps/email-sentinel/data/projects.json
```

**Why it matters here.** The immediate blast radius is small — `/home/jayserver` is `drwxr-x---`, so no other local account can traverse in, and `config/` is mounted `:ro`. But `data/` is mounted read-write into containers running as uid 1000, `docker-compose.yml` and `Dockerfile` are themselves 777, and `orchestrator/*.py` is code that gets baked into the image. This is the "mistakes" leg of your threat model: it means any process that gets a foothold as any uid, or any future second local account, can rewrite code that later runs with access to the secrets.

**Fix (no root, safe):**
```bash
find ~/jay-os -type d -exec chmod 755 {} +
find ~/jay-os -type f -exec chmod 644 {} +
find ~/jay-os -type f -name '*.sh' -exec chmod 755 {} +
chmod 700 ~/jay-os/secrets && chmod 600 ~/jay-os/secrets/*
chmod 600 ~/jay-os/apps/email-sentinel/deploy/.env*
```
**Disruption:** none — the sentinel containers read `config/` and write `data/` as uid 1000, which owns all of it. Also tighten `~/.claude` (`775`) and `~/.claude/settings.json` (`664`) to `700`/`600`.

### M5. Stale `.bak` copies of secrets and config sit beside the originals

```
-rw------- 1 jayserver jayserver 1191 deploy/.env.bak          # 600, same key names as .env
-rwxrwxr-x 1 jayserver jayserver 6497 config/sentinel.toml.bak # 775
-rw------- 1 root root      4106 /srv/stack/adguard/conf/AdGuardHome.yaml.bak
$ git status --porcelain
?? config/sentinel.toml.bak
?? deploy/.env
?? deploy/.env.bak
```

**Why it matters here.** `.env.bak` holds an older copy of the Postgres password and SMTP app password. If either was rotated, the old value is still on disk and in the restic snapshot, and it is one careless `git add -A` from being committed — `.gitignore` covers `config/sentinel.toml` but **not** `deploy/.env`, `*.bak`, or `deploy/.env.bak`. (It is currently untracked; `git ls-files --error-unmatch deploy/.env` → not known to git, and no `.env` has ever been committed across all 14 commits.)

**Fix (no root):** delete the backups once you have confirmed the live values work, and close the `.gitignore` gap:
```
echo -e '.env\n.env.*\n!.env.example\n*.bak' >> ~/jay-os/apps/email-sentinel/.gitignore
```

### M6. Images are tag-pinned, not digest-pinned, and one is 17 months stale

```
louislam/dockge:1              17 months ago     <-- see H1
adguard/adguardhome:v0.107.79  pinned, good
docker.n8n.io/n8nio/n8n:2.37.7 pinned, good
postgres:16-alpine / 17-alpine / mariadb:11 / redis:7-alpine / nextcloud:34-apache
hello-world:latest             leftover test image
```
`unattended-upgrades` covers apt packages only — nothing updates these. **Fix:** schedule a monthly `docker compose pull && up -d` per stack (Watchtower is *not* a good fit here given the docker.sock exposure), and `docker rmi hello-world adguard/adguardhome:latest nextcloud:apache` to clear the untagged duplicates. Pin by digest for the images that matter.

---

## Low

- **L1. GitHub deploy key is unencrypted on disk.** `~/.ssh` is `0700`, `authorized_keys` has exactly **1** key (`jayserver-admin@jayserver-20260906`, ED25519), and no admin *private* key is present — only the `.pub`. The one private key, `id_ed25519_github_sentinel` (0600), has no passphrase (`ssh-keygen -y -P ''` succeeds). Its comment reads `deploy key: JAY-OS-Email-Sentinel (read-only)`, so the exposure is read access to one private repo. Acceptable for automation; verify in GitHub that the deploy key really is read-only.
- **L2. Stale git credential helper.** `credential.helper = /mnt/c/Program Files/Git/mingw64/bin/git-credential-manager.exe` — a WSL path that does not exist here. Harmless but dead; the remote is `git@github.com:heverlopez8-cloud/JAY-OS-Email-Sentinel.git` (SSH, **no embedded token**). Clear with `git config --unset credential.helper`.
- **L3. Shell history is clean but short-lived.** `HISTCONTROL=ignoreboth`, `HISTFILESIZE=2000`, 81 lines, `0600`. Zero occurrences of either password (see "Checked and fine"). Three `curl ... | sh`-shaped lines are present — worth reviewing what was piped to a shell.
- **L4. Unnecessary services running.** `ModemManager`, `bolt` (Thunderbolt), `udisks2`, `upower`, `fwupd`, `multipathd`, `snapd`, `getty@tty1`. None listen on the network, so the risk is attack surface rather than exposure. `sudo systemctl disable --now ModemManager multipathd` is safe on this hardware (**needs root**).
- **L5. `X11Forwarding yes`** in `/etc/ssh/sshd_config` on a headless server. Set `X11Forwarding no` (**needs root**).
- **L6. AdGuard serves DNS to the LAN and its admin UI binds `*:3000`.** `network_mode: host` with `cap_add: NET_BIND_SERVICE` (correctly *not* privileged). UFW is confirmed blocking both from the LAN (`DPT=53` ×57, `DPT=3000` ×27 in the kernel log), so the admin UI is reachable only from the tailnet — where every device can reach it (H2). It does present a login page (`GET :3000/` → 302 → `/login.html`). Note the LAN DNS blocks mean AdGuard is currently not usable as a LAN resolver.
- **L7. Nextcloud cannot reach itself.** 28 `UFW BLOCK` entries show `SRC=172.18.0.4 → DST=100.85.8.63:8080` — a container resolving its own external URL and being dropped. Operational rather than security, but it will break federation/preview features.
- **L8. Core dumps go to apport.** `core_pattern` pipes to `/usr/share/apport/apport`; `/var/lib/apport/coredump` is empty. A crash in a process holding the SMTP password could write it to disk. Consider `ulimit -c 0` hardening if you ever disable apport's default suppression.
- **L9. Port scan from 192.168.0.66 during this review.** At 23:21 today, `192.168.0.66` (your Windows desktop, `MONEYMAKER`) SYN-scanned ports 3000, 5678, 5001, 37500 and 5432; all were dropped by UFW. Presumably you. Flagging it only so it is not mistaken for something else later.

---

## Fix these five first

1. **Kill SSH password auth** — `sudo rm /etc/ssh/sshd_config.d/00-jay.conf && sudo sshd -t && sudo systemctl reload ssh`. One command, closes the widest door, keeps your working key path. *(C1)*
2. **Close the tailnet's blanket trust** — `sudo tailscale set --ssh=false`, write a real Tailscale ACL, and replace `ufw allow in on tailscale0` with per-port rules. Without this, step 1 is only half the perimeter. *(H2)*
3. **Get Dockge off the root socket** — stop it when unused, or front it with a socket proxy; it is a 17-month-old image holding a writable `docker.sock` on the tailnet. *(H1)*
4. **Make backups real** — install the two timer units, move the restic repo off-box, move `.restic-pass` out of the backup set, run `sudo backup.sh` once for a first complete snapshot, and add a dead-man ping. Right now you have no working backup and no alerting. *(H6)*
5. **Harden the sentinel containers** — add `cap_drop: [ALL]`, `no-new-privileges:true`, `read_only: true`, and confirm whether `SENTINEL_UID=1000` is deliberate. Cheap, and it directly narrows the malicious-dependency path to the mail key. *(H4)*

---

## Could not verify without root

- `sshd -T` effective config, and the contents of `/etc/ssh/sshd_config.d/50-cloud-init.conf` (`0600 root:root`) — it may also set `PasswordAuthentication`. Findings on sshd are derived from the readable drop-ins plus `auth.log` evidence.
- The live `ufw`/`nftables` ruleset. `ufw status` and `nft list ruleset` both require root. I established UFW is active from `/etc/ufw/ufw.conf` (`ENABLED=yes`), `systemctl is-active ufw` (`active`), 2 700+ `[UFW BLOCK]` kernel entries this boot, and the ruleset recorded in `~/harden-d.log` at 01:07 — but I cannot confirm that ruleset is still what is loaded, nor inspect the `DOCKER`/`DOCKER-USER` chains.
- Whether Nextcloud on `192.168.0.48:8080` is genuinely reachable from another LAN host. My `curl` originated on this box (loopback path). The conclusion in M2 rests on Docker's DNAT/FORWARD behaviour plus the absence of any LAN-sourced `DPT=8080` block.
- `/var/log/btmp` (failed login attempts) — permission denied to `lastb`. `auth.log` shows only 3 `Failed password` events this boot.
- AdGuard's configuration and admin credentials — `AdGuardHome.yaml` is `0600 root:root`.
- Whether any `NOPASSWD` entries exist in `/etc/sudoers.d/`. `sudo -n true` failed, which proves none apply to `true` for this user, but the files are unreadable.
- `/srv/data/nextcloud` contents and permissions (`root:root` parent), `/srv/stack/n8n/pgdata` (permission denied), and n8n's stored third-party credentials.
- The owner of the listener on `100.85.8.63:37500` — `ss` shows no PID, so it belongs to root or another user. It is tailnet-bound and UFW-blocked from the LAN. Not `tailscale serve` (`No serve config`).

## Checked and fine

- **No secret values leaked anywhere I could search.** All counts zero: `docker compose logs | grep -c 'postgresql://sentinel:'` → **0**; compose logs containing the Postgres password → **0** of 312 lines; containing the SMTP app password → **0**; `~/.bash_history` → **0** and **0** of 81 lines; `~/.claude/history.jsonl` → **0**; `~/.claude/projects/**` → **0** files; `journalctl --user` (100 000 lines) → **0**; `~/jay-os/audits/**` → **0**.
- **Nothing sensitive in git.** 14 commits, one repo (`apps/email-sentinel/.git`). `deploy/.env` is untracked and has never been committed; tracked config files are `.example` variants only. Remote is SSH with no embedded token.
- **`sentinel.toml` contains no secrets** — only `service_account_key` and `token_path` *paths*. `shadow = false` (live, as stated). Eight mailboxes declared; five enabled (`info@`, `jay@`, `eric@`, `projects@`, `guillermo@`), three disabled (`support@`, `permits@`, `aaron@`).
- **Postgres is not exposed.** No `ports:` in compose, `docker inspect` shows `PORTS: {}`, and `/dev/tcp/127.0.0.1/5432` → connection refused. Both Postgres instances and MariaDB/Redis publish nothing.
- **Secret file permissions are correct.** `~/jay-os/secrets` `0700`; both JSONs `0600`. `deploy/.env` and `.env.bak` `0600`. `/srv/stack/n8n/.env` and `/srv/stack/nextcloud/.env` `0600`. `~/.claude.json` `0600`. `~/.claude/.credentials.json` `0600`. `~/.ssh` `0700` with all private material `0600`. `~/` is `drwxr-x---`.
- **Patching is healthy.** `apt list --upgradable` → **0** packages, **0** security. `unattended-upgrades` active with security origins enabled; last run 06:42 today. No `/var/run/reboot-required`. Kernel 6.8.0-139 on Ubuntu 24.04.4.
- **No passwordless sudo.** `sudo -n true` → "a password is required". `PermitRootLogin no` is in effect (nothing earlier in the include order overrides it). Only one non-system account exists; root has no separate login path.
- **Setuid inventory is stock.** 13 setuid binaries, all standard Ubuntu (`passwd`, `su`, `sudo`, `mount`, `umount`, `chsh`, `chfn`, `gpasswd`, `newgrp`, `fusermount3`, `ssh-keysign`, polkit and dbus helpers). No unexpected setgid or file capabilities.
- **No privileged containers.** `Privileged=false` on all 13. Only AdGuard adds a capability (`NET_BIND_SERVICE`, needed for port 53). Docker socket is `srw-rw---- root:docker` — not world-accessible. No `daemon.json` overrides.
- **Compose secrets handling is deliberate and documented** — Postgres deliberately unpublished, `config/` and `secrets/` mounted `:ro`, and the uid trade-off explained in comments rather than left implicit.
- **journald is persistent** (`/var/log/journal` exists, 80 MB) and log rotation is scheduled. Disk is at 3% (22G of 915G) — no pressure.
- **No cron jobs** for this user; `/etc/cron.d` contains only stock entries. `~/.docker/config.json` does not exist, so no cached registry credentials.
- **Tailscale node key expires 2027-03-06**, node is not an exit node, `RouteAll: false`, `AdvertiseRoutes: null` — it is not routing or exposing the LAN to the tailnet.
