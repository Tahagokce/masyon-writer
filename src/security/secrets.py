import base64
import json
import os
import re
from urllib.parse import quote, quote_plus


class Secrets:
    def __init__(self, names: list[str]):
        self.values = {name: os.environ[name] for name in names if os.environ.get(name)}
        self.variants = set()
        for value in self.values.values():
            self.variants.update((value, quote(value, safe=""), quote_plus(value),
                                  json.dumps(value)[1:-1], base64.b64encode(value.encode()).decode()))

    def clean(self, value: str) -> str:
        for secret in sorted(self.variants, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(bearer\s+)[\w.~/+\-=]+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)(https?://)[^\s/]+:[^\s/@]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)([?&][^=\s]*(?:token|secret|key|session|auth)[^=\s]*=)[^&#\s'\"]+", r"\1[REDACTED]", value)
        value = re.sub(r'''(?i)(["']?(?:password|authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?(?:id|secret))["']?\s*[:=]\s*)(["'])(.*?)\2''',
                       r"\1\2[REDACTED]\2", value)
        value = re.sub(r"(?i)((?:token|api[_-]?key|password|authorization|cookie)\s*[=:]\s*)[^\s,;]+",
                       r"\1[REDACTED]", value)
        return value

    def object(self, value):
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, dict):
            return {key: self.object(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.object(item) for item in value]
        return value

    def contains(self, text: str) -> bool:
        return any(value in text for value in self.variants)

    def diagnostic(self, exc):
        message = getattr(exc, "message", None) or str(exc)
        message = re.split(r"\n(?:Call log:|\s+at |\s*\d+\))", message)[0]
        return {"errorClass":type(exc).__name__, "error":self.clean(message)[:700]}
