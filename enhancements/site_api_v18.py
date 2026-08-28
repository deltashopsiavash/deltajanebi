"""Delta bot API v18: preserve v17 endpoints and activate v22 catalog policy."""

from django.views.decorators.csrf import csrf_exempt

# Load the complete existing API chain first, then install v22 category,
# identity, stock aggregation and strict source-image behavior behind it.
from .site_api_v17 import bot_api as v17_bot_api
from shop.services import source_catalog_v22  # noqa: F401,E402


@csrf_exempt
def bot_api(request):
    return v17_bot_api(request)
