"""Delta bot API v18: preserve v17 endpoints and activate v21 catalog sync."""

# Load the complete existing API chain first (including its v20 bridge), then
# replace only source-catalog implementation functions with v21.
from .site_api_v17 import bot_api as v17_bot_api
from shop.services import source_catalog_patch_v21  # noqa: F401,E402


def bot_api(request):
    return v17_bot_api(request)
