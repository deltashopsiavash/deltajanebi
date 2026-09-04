import re

from django.utils.html import escape

from .models import AddonSetting


_TITLE_RE = re.compile(br"<title\b[^>]*>.*?</title>", re.IGNORECASE | re.DOTALL)


class SiteTitleOverrideMiddleware:
    """Replace the rendered HTML <title> while a manager override is active.

    This is intentionally independent from the visible store name, so temporary
    domain-verification codes can be placed in the browser/page title without
    renaming the shop anywhere else.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(response, "streaming", False):
            return response
        if response.status_code < 200 or response.status_code >= 400:
            return response
        if "text/html" not in str(response.get("Content-Type", "")).lower():
            return response

        try:
            title = (AddonSetting.load().site_title_override or "").strip()
        except Exception:
            return response
        if not title:
            return response

        charset = getattr(response, "charset", None) or "utf-8"
        replacement = f"<title>{escape(title)}</title>".encode(charset, errors="xmlcharrefreplace")
        content, count = _TITLE_RE.subn(lambda _match: replacement, response.content, count=1)
        if not count:
            return response
        response.content = content
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(content))
        return response
