# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Jardias, please **do not** file a
public issue. Instead, report it privately to the project maintainers:

- **Contact**: See [AUTHORS](AUTHORS) for maintainer contact information
- **Response time**: You can expect an initial response within 7 days
- **Disclosure**: We follow coordinated disclosure — please allow us time to
  release a fix before making the vulnerability public

## Scope

Security-relevant topics include but are not limited to:

- API key leakage (actual keys appearing in logs, history files, or git history)
- Prompt injection vulnerabilities that bypass built-in safeguards
- Unauthorized access to character data or memory stores
- Remote code execution via tool dispatch
- Dependency vulnerabilities with known CVEs affecting Jardias

## Out of Scope

- Vulnerabilities in third-party LLM providers (MiniMax, DeepSeek, DashScope)
- Social engineering attacks against individual users
- Theoretical attacks that require physical access to the user's machine

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

Jardias is currently in reference implementation stage. Only the latest
commit on the default branch receives security patches.

## Security Best Practices for Users

- Never commit your `.env` file — it is already in `.gitignore`
- Rotate API keys regularly
- Review `character_data/*/history.json` before sharing — they may contain
  personal information from your conversations
- Run Jardias in a virtual environment with minimal system privileges
