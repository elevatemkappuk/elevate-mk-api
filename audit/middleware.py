from audit.services import build_http_provenance, reset_audit_provenance, set_audit_provenance


class AuditProvenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_audit_provenance(build_http_provenance(request))
        try:
            return self.get_response(request)
        finally:
            reset_audit_provenance(token)
