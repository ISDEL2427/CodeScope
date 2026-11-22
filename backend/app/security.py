from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|authorization)"
    r"(\s*[:=]\s*)(['\"])(.*?)(\3)"
)
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY = re.compile(r"\b(sk-(?:proj-)?)[A-Za-z0-9_-]{8,}\b")
_PEM = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive_text(value: str) -> str:
    value = _PEM.sub("-----BEGIN PRIVATE KEY----- [REDACTED] -----END PRIVATE KEY-----", value)
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: (
            match.group(1) + match.group(2) + match.group(3) + "[REDACTED]" + match.group(5)
        ),
        value,
    )
    value = _BEARER.sub(r"\1[REDACTED]", value)
    return _OPENAI_KEY.sub(r"\1[REDACTED]", value)


def sanitize_external_message(message: str, *secrets: str) -> str:
    sanitized = redact_sensitive_text(message)
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")

    def clean_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            if not parsed.username and not parsed.password and not parsed.query:
                return raw
            hostname = parsed.hostname or ""
            netloc = hostname + (f":{parsed.port}" if parsed.port else "")
            return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        except ValueError:
            return "[REDACTED URL]"

    return re.sub(r"https?://[^\s]+", clean_url, sanitized)[:2000]


def contains_secret(value: object, secret: str) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(contains_secret(key, secret) or contains_secret(item, secret) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item, secret) for item in value)
    return False
