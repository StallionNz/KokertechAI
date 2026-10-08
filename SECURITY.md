# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

Security and privacy are core architectural tenets of KokertechAI. If you discover a security vulnerability, please report it responsibly rather than opening a public issue.

### Contact
Please send vulnerability reports directly to:
**`kokertechnz@gmail.com`**

Include:
1. Description of the vulnerability.
2. Steps to reproduce or proof-of-concept.
3. Potential impact and affected components.

You can expect an initial acknowledgement within 48 hours.

## Local Data & Secrets Guidelines

- **Zero Telemetry**: KokertechAI runs 100% offline and in-process. It does not send telemetry or analytics to any remote server.
- **Secrets & API Keys**: Always supply secrets through `.env` or the desktop settings dialog. Never commit your `.env` file or SQLite databases (`*.db`) to version control.
- **Diagnostic Bundles**: When sharing bug reports or logs, ensure you do not include local file paths or proprietary documents.
