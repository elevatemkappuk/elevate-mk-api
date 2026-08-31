import hashlib
import json


def fingerprint_source(value):
    """Return a SHA-256 fingerprint for canonical JSON-like source evidence."""
    canonical_value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical_value.encode("utf-8")).hexdigest()


def fingerprint_source_row(raw_data):
    return fingerprint_source(raw_data)


def fingerprint_bytes(source_bytes):
    return hashlib.sha256(source_bytes).hexdigest()
