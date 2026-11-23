import re


_NAME_BOUNDARY = re.compile(r"(^|[\s'\-])([^\W\d_])", re.UNICODE)


def normalize_community_name(value):
    """Trim/collapse Community names and conservatively case obvious input."""
    normalized = " ".join(str(value).strip().split())
    if not normalized or not (normalized.islower() or normalized.isupper()):
        return normalized
    lowercase = normalized.lower()
    return _NAME_BOUNDARY.sub(lambda match: f"{match.group(1)}{match.group(2).upper()}", lowercase)
