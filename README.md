# PipeChecker

> CI pipelines for your entire infrastructure — not just your code.

PipeChecker brings the pipeline model of GitLab CI to anything you can connect to: servers, containers, websites, and more. Define steps, run checks, and optionally auto-remediate failures — all driven by YAML and a pluggable connector system.

## Concept

Most CI tools are tied to a code repository. PipeChecker decouples pipelines from source control so you can run them against **any target** — a fleet of LXC containers, a list of reverse-proxy URLs, a set of machines. Connectors discover targets dynamically, and pipelines run checks against each one.

```
Connector → Targets → Pipeline → Steps → Results
```

- A **connector** queries a source (Proxmox, Caddy config, …) and returns a list of targets.
- A **pipeline** defines an ordered sequence of steps with dependency resolution.
- Each **step** runs a command on the target, validates the output, and optionally runs a fix command if the check fails.

## Connectors

| Connector | What it discovers                                                  |
|-----------|--------------------------------------------------------------------|
| `proxmox` | LXC containers on a Proxmox node (via SSH)                         |
| `caddy`   | Reverse-proxy upstream URLs from a Caddyfile (local, HTTP, or SSH) |

## Pipeline steps

Steps are the building blocks of a pipeline. Each step runs a command and validates its result:

```yaml
- id: check_curl
  exec: which curl
  check_method: exit_code
  if_failed: apt-get install -y curl
  requires: []
```

**Check methods:**

| Method             | Passes when                          |
|--------------------|--------------------------------------|
| `exit_code`        | Command exits with `0`               |
| `stderr_empty`     | stderr produces no output            |
| `stdout_not_empty` | stdout has any content               |
| `stdout_contains`  | stdout contains `check_pattern`      |
| `stdout_regex`     | stdout matches `check_pattern` regex |

Steps can declare `requires` to enforce execution order. The runner resolves dependencies topologically and skips dependents if a required step fails.

## Example

```yaml
# save/pipelines/my_pipeline.yaml
name: base_health
connectors:
  - my_proxmox
runner: proxmox_ct
pipeline:
  - id: check_curl
    exec: which curl
    check_method: exit_code
    if_failed: apt-get install -y curl

  - id: check_service
    exec: systemctl is-active myservice
    check_method: stdout_contains
    check_pattern: active
    requires:
      - check_curl
```

## Installation

### Requirements

- Python 3.14+
- SSH key-based access to the machines you want to monitor (no password prompts)

### 1. Clone and install dependencies

```bash
git clone https://github.com/AlexAuragan/PipeChecker.git
cd PipeChecker
uv sync          # or: pip install -e .
```

### 2. Generate credentials

PipeChecker requires credentials to be configured before it will start. Run the setup command to generate an API key, a web UI password, and a session signing secret all at once:

```bash
python cli.py setup
```

This will:
- Print a one-time **API key** and **web UI password** — save these somewhere safe, they are not stored in plaintext
- Write the corresponding hashes to a `.env` file in the project root

Example output:
```
✔ API key generated
  Key (save this, it won't be shown again): pc_xxx...
✔ Web UI credentials generated
  Username : admin
  Password : pc_yyy...  (save this, it won't be shown again)
  Credentials written to: .env
```

If you need to rotate only the web password later:
```bash
python cli.py generate-web-password
# then update PIPECHECKER_WEB_PASSWORD_HASH and PIPECHECKER_WEB_SECRET in your .env
```

If you need to rotate only the API key:
```bash
python cli.py generate-key
# then update PIPECHECKER_API_KEY_HASH in your .env
```

### 3. Start the server

Load the `.env` file and start the server:

```bash
set -a && source .env && set +a
fastapi run src/api/api.py --host 0.0.0.0 --port 8000
```

Or with uvicorn directly:

```bash
set -a && source .env && set +a
uvicorn src.api.api:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser and log in with `admin` and the password printed during setup.

### 4. (Optional) Install as a systemd service

Re-run setup as root to have it write and enable a systemd service automatically:

```bash
sudo python cli.py setup --dir /path/to/PipeChecker --port 8000
```

The service will be enabled on boot and started immediately. Useful commands:

```bash
systemctl status pipechecker
journalctl -u pipechecker -f
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `PIPECHECKER_API_KEY_HASH` | Yes | pbkdf2 hash of the REST API key (`salt_hex:dk_hex`) |
| `PIPECHECKER_WEB_PASSWORD_HASH` | Yes | pbkdf2 hash of the web UI password |
| `PIPECHECKER_WEB_USER` | No | Web UI username (default: `admin`) |
| `PIPECHECKER_WEB_SECRET` | No | HMAC signing key for session cookies (auto-generated per restart if unset — sessions will not survive restarts) |

The server refuses to start if either required variable is missing and prints the exact command to fix it.

---

## API authentication

All REST API endpoints (`/api/v1/...`) require an `X-API-Key` header:

```bash
curl -H "X-API-Key: pc_xxx..." http://localhost:8000/api/v1/pipelines
```

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — free to use, modify, and distribute for any non-commercial purpose.
Commercial use is not permitted.

## TODO
 * [x] Implement connector > pipeline > run
 * [ ] Connectors
   * [x] Proxmox
   * [x] Caddy
   * [ ] Websites
   * [ ] Github / gitlab / gitea
 * [ ] Runners
   * [x] Server side cron for all pipelines
   * [x] Proxmox
   * [x] Machine
   * [ ] Website
 * [x] Run loop
 * [x] Run history
   * [ ] Long term view
 * [ ] Set gravity for each pipeline or step (if it fails is it an error or just a warning ?)
 * [ ] Set up notification system (webhook, rss).
 * [ ] Display info on step hover
 * [x] Login method
 * [ ] Targets should be lazy loaded (loaded when a pipeline is run) and cached.