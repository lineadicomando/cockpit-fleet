# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x (current) | Yes |

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Send a report to **alessandro.gagliano@gmail.com** with:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional)

You will receive a response within 5 business days. Once the issue is confirmed and a fix is available, a security advisory will be published and you will be credited (unless you prefer to remain anonymous).

## Security model

Whoever can launch a playbook is effectively root on the managed nodes, so the
design keeps the controller's own privileged surface as small as possible:

- No daemon and no listening port. Runs execute in sandboxed systemd units
  under the unprivileged `cockpit-fleet` account.
- Two helpers run as root, both a few dozen lines: `fleet-unit` starts or stops
  an instance of the packaged run unit, `fleet-secret` encrypts a secret read
  from stdin. Neither accepts a path, an option or a unit name from its caller.
- Secrets are encrypted with `systemd-creds` and can only be decrypted by PID 1,
  which hands them to the one run that references them. They cannot be read
  back, by the web interface or by the service account.
- A run sees its own directory only: not the database, not other projects, not
  other runs, not the system bus.

See [docs/architecture.md](docs/architecture.md) for the details and for how
each of these properties is tested.
