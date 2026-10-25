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
design keeps the controller's own privileged surface as small as possible: no
daemon and no listening port, runs in sandboxed systemd units under an
unprivileged account, two small root helpers that take one identifier each,
and secrets that only PID 1 can decrypt, for the one run that needs them.

What it protects, from whom, and its known limits are in
[docs/security.md](docs/security.md); how each property is built and tested is
in [docs/architecture.md](docs/architecture.md).
