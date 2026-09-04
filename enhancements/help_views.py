from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .help_pages import ensure_default_help_pages
from .models import HelpPage


def terms(request):
    ensure_default_help_pages()
    page = HelpPage.objects.filter(slug="rules").first()
    if not page:
        raise Http404
    return render(request, "shop/help_page.html", {"help_page": page})


def help_page(request, slug):
    ensure_default_help_pages()
    page = get_object_or_404(HelpPage, slug=slug, is_visible=True)
    return render(request, "shop/help_page.html", {"help_page": page})
