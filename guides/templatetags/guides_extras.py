import markdown
import nh3
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "h5", "h6", "strong", "em", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "table", "thead", "tbody", "tr", "th", "td", "del",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}
ALLOWED_SCHEMES = {"http", "https", "mailto"}


@register.filter
def markdown_safe(text):
    """Markdown -> HTML, renset med nh3 (ingen scripts, billeder, iframes eller javascript:-links)."""
    # Overskrifter flyttes ned (# -> h3), fordi afsnittets egen titel er en h2.
    html = markdown.markdown(
        text or "",
        extensions=["fenced_code", "tables", "sane_lists", "nl2br", "toc"],
        extension_configs={"toc": {"baselevel": 3}},
    )
    clean = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
    return mark_safe(clean)
