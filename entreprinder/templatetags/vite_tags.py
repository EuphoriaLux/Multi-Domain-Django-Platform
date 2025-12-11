import json
import os
from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.safestring import mark_safe

register = template.Library()

@register.simple_tag
def vite_asset(entry_name):
    """
    Load a Vite-built asset using the manifest file.

    Usage in templates:
    {% load vite_tags %}
    {% vite_asset 'pixel-war-pixi' %}
    """
    # Always try to use built assets first (even in DEBUG mode)
    # This allows us to use the build system during development
    manifest_path = os.path.join(
        settings.BASE_DIR,
        'entreprinder', 'vibe', 'static', 'vibe', 'js', 'dist', 'manifest.json'
    )

    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        if entry_name in manifest:
            built_filename = manifest[entry_name]
            asset_url = static(f'vibe/js/dist/{built_filename}')
            return mark_safe(f'<script type="module" src="{asset_url}"></script>')
        else:
            # Fallback to original file if not in manifest
            if settings.DEBUG:
                asset_url = static(f'vibe/js/{entry_name}.js')
                return mark_safe(f'<script type="module" src="{asset_url}"></script>')
            else:
                return mark_safe(f'<!-- Vite asset {entry_name} not found in manifest -->')

    except (FileNotFoundError, json.JSONDecodeError) as e:
        # Fallback to original file if manifest not found (dev mode only)
        if settings.DEBUG:
            asset_url = static(f'vibe/js/{entry_name}.js')
            return mark_safe(f'<script type="module" src="{asset_url}"></script>')
        else:
            return mark_safe(f'<!-- Vite manifest error: {e} -->')

@register.simple_tag
def vite_chunk_asset(chunk_name):
    """
    Load Vite-built chunk assets (like pixi-core, pixi-viewport chunks).

    Usage in templates:
    {% vite_chunk_asset 'pixi-core' %}
    """
    if settings.DEBUG:
        return ''  # Chunks are handled automatically in development

    manifest_path = os.path.join(
        settings.BASE_DIR,
        'entreprinder', 'vibe', 'static', 'vibe', 'js', 'dist', 'manifest.json'
    )

    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        # Look for chunk files
        chunk_files = [f for f in manifest.values() if chunk_name in f and f.endswith('.chunk.js')]

        scripts = []
        for chunk_file in chunk_files:
            asset_url = static(f'vibe/js/dist/{chunk_file}')
            scripts.append(f'<link rel="modulepreload" href="{asset_url}">')

        return mark_safe(''.join(scripts))

    except (FileNotFoundError, json.JSONDecodeError):
        return ''

@register.simple_tag
def vite_preload_deps():
    """
    Preload critical dependencies for better performance.
    """
    deps = [
        'pixi-core',
        'pixi-viewport',
        'mobile-helpers'
    ]

    preload_tags = []
    for dep in deps:
        preload_tags.append(vite_chunk_asset(dep))

    return mark_safe(''.join(preload_tags))
