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

| Connector | What it discovers |
|-----------|-------------------|
| `proxmox` | LXC containers on a Proxmox node (via SSH) |
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

| Method | Passes when |
|--------|-------------|
| `exit_code` | Command exits with `0` |
| `stderr_empty` | stderr produces no output |
| `stdout_not_empty` | stdout has any content |
| `stdout_contains` | stdout contains `check_pattern` |
| `stdout_regex` | stdout matches `check_pattern` regex |

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

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — free to use, modify, and distribute for any non-commercial purpose. Commercial use is not permitted.

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
   * [ ] Machine
   * [ ] Website
 * [x] Run loop
 * [x] Run history
 * [x] API
 * [x] Web interface