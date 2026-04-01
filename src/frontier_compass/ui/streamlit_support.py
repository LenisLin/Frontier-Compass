"""Streamlit UI compatibility helpers."""

from __future__ import annotations

from html import escape
import inspect
from typing import Any

import streamlit as st


def render_external_link(
    label: str,
    url: str,
    *,
    help: str | None = None,
    key: str | None = None,
    type: str | None = None,
    use_container_width: bool | None = None,
) -> bool:
    """Render a resilient outbound-link control for the active Streamlit version."""
    if not url:
        return False

    link_button = getattr(st, "link_button", None)
    if callable(link_button):
        kwargs = _supported_link_button_kwargs(
            link_button,
            help=help,
            key=key,
            type=type,
            use_container_width=use_container_width,
        )
        try:
            link_button(label, url, **kwargs)
            return True
        except TypeError:
            pass

    st.markdown(_build_external_link_markup(label, url, help=help, type=type), unsafe_allow_html=True)
    return False


def _supported_link_button_kwargs(link_button: Any, **optional_kwargs: Any) -> dict[str, Any]:
    try:
        supported_parameters = set(inspect.signature(link_button).parameters)
    except (TypeError, ValueError):
        return {}

    return {
        name: value
        for name, value in optional_kwargs.items()
        if value is not None and name in supported_parameters
    }


def _build_external_link_markup(
    label: str,
    url: str,
    *,
    help: str | None = None,
    type: str | None = None,
) -> str:
    escaped_label = escape(label)
    escaped_url = escape(url, quote=True)
    escaped_help = escape(help, quote=True) if help else ""
    link_class = "fc-link-fallback-link"
    if type == "primary":
        link_class += " fc-link-primary"
    title_attr = f' title="{escaped_help}"' if escaped_help else ""
    return (
        '<div class="fc-link-fallback">'
        f'<a class="{link_class}" href="{escaped_url}" target="_blank" rel="noopener noreferrer"{title_attr}>'
        f"{escaped_label}</a>"
        "</div>"
    )
