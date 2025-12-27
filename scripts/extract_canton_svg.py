"""
Extract and process canton paths from Wikimedia Commons Luxembourg SVG.
Creates a simplified SVG with proper IDs for the Crush.lu canton map.

Strategy: Border regions are rendered ON TOP of cantons but with clip-path
to only show/click in areas outside Luxembourg's boundary.
"""

import re
from pathlib import Path


# Canton mapping based on geographic analysis (path index -> canton id, name)
# Paths are numbered 1-12 as they appear in the SVG
CANTON_MAPPING = {
    12: ('canton-clervaux', 'Clervaux'),      # x=138.3, y=290.4 - Northmost
    10: ('canton-vianden', 'Vianden'),         # x=343.1, y=389.9 - Northeast
    11: ('canton-wiltz', 'Wiltz'),             # x=88.9, y=390.8 - Northwest
    8: ('canton-redange', 'Redange'),          # x=79.4, y=487.6 - West
    9: ('canton-diekirch', 'Diekirch'),        # x=258.4, y=494.2 - North-central
    6: ('canton-echternach', 'Echternach'),    # x=461.3, y=572.1 - East
    7: ('canton-mersch', 'Mersch'),            # x=267.2, y=620.3 - Central
    5: ('canton-grevenmacher', 'Grevenmacher'),# x=415.9, y=689.9 - East-central
    4: ('canton-luxembourg', 'Luxembourg'),    # x=276.1, y=807.1 - Capital
    3: ('canton-capellen', 'Capellen'),        # x=161.0, y=808.5 - West-central
    2: ('canton-esch', 'Esch-sur-Alzette'),    # x=164.4, y=827.5 - Southwest
    1: ('canton-remich', 'Remich'),            # x=453.1, y=830.9 - Southeast
}

# Border region definitions - each is a rectangle that covers one side/corner
# These will be clipped to only show outside Luxembourg
BORDER_REGIONS = {
    'border-belgium': {
        'name': 'Belgium (Arlon/Virton area)',
        'class': 'border-belgium',
        # West side - covers the entire left portion
        'rect': (57, 75, 140, 910),  # x, y, width, height
    },
    'border-germany-trier': {
        'name': 'Germany (Trier area)',
        'class': 'border-germany',
        # Northeast - top right area
        'rect': (400, 75, 297, 500),
    },
    'border-germany-saarland': {
        'name': 'Germany (Saarland)',
        'class': 'border-germany',
        # Southeast - bottom right area
        'rect': (400, 575, 297, 410),
    },
    'border-france': {
        'name': 'France (Thionville/Metz area)',
        'class': 'border-france',
        # South side - bottom portion
        'rect': (57, 800, 400, 185),
    },
}


def extract_canton_paths():
    """Extract the 12 canton paths from the downloaded SVG."""

    svg_path = Path(__file__).parent.parent / "Cantons_du_Luxembourg.svg"
    content = svg_path.read_text(encoding='utf-8')

    # Find the Kantone group
    kantone_start = content.find('<g id="Kantone">')
    grenzen_start = content.find('<g id="Grenzen">')

    if kantone_start == -1 or grenzen_start == -1:
        print("Could not find canton groups")
        return None

    kantone_section = content[kantone_start:grenzen_start]

    # Extract all paths with fill colors
    path_pattern = r'<path\s+fill="(#[A-Fa-f0-9]{6})"\s+d="([^"]+)"'
    paths = re.findall(path_pattern, kantone_section, re.DOTALL)

    print(f"Found {len(paths)} paths in Kantone section")

    return paths


def generate_canton_svg_template(paths):
    """Generate the Django template SVG with proper canton IDs.

    Strategy:
    1. Background rect for overall map area
    2. Luxembourg cantons rendered first (bottom layer)
    3. Border regions rendered ON TOP but clipped to only show OUTSIDE Luxembourg
    4. This allows clicking on exposed border areas while cantons remain clickable
    """

    output_lines = []

    # Collect all canton paths to create a combined Luxembourg outline for clipping
    canton_paths_data = []
    for i, (fill, path_d) in enumerate(paths, 1):
        if i in CANTON_MAPPING:
            clean_path = ' '.join(path_d.split())
            canton_paths_data.append(clean_path)

    output_lines.append('''{# SVG Map of Luxembourg Cantons with Border Regions #}
{# Used with the cantonMap Alpine.js component #}
{% load i18n %}
<svg id="canton-map-svg"
     viewBox="57 75 640 910"
     role="listbox"
     aria-label="{% trans 'Select your region on the map' %}"
     class="canton-map-svg"
     tabindex="0">
    <title>{% trans 'Luxembourg Canton Selection Map' %}</title>
    <desc>{% trans 'Interactive map showing Luxembourg cantons and border regions. Click or use arrow keys to select your location.' %}</desc>

    <!-- Definitions for gradients and effects -->
    <defs>
        <!-- Drop shadow for selected regions -->
        <filter id="drop-shadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="2" dy="2" stdDeviation="3" flood-color="#8E44AD" flood-opacity="0.4"/>
        </filter>

        <!-- Glow effect for hover -->
        <filter id="hover-glow" x="-10%" y="-10%" width="120%" height="120%">
            <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
            <feMerge>
                <feMergeNode in="coloredBlur"/>
                <feMergeNode in="SourceGraphic"/>
            </feMerge>
        </filter>

        <!-- Border region gradients -->
        <linearGradient id="border-belgium-gradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" style="stop-color:#E8DAEF;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#D2B4DE;stop-opacity:1" />
        </linearGradient>
        <linearGradient id="border-germany-gradient" x1="100%" y1="0%" x2="0%" y2="0%">
            <stop offset="0%" style="stop-color:#D4E6F1;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#AED6F1;stop-opacity:1" />
        </linearGradient>
        <linearGradient id="border-france-gradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" style="stop-color:#FADBD8;stop-opacity:1" />
            <stop offset="100%" style="stop-color:#F5B7B1;stop-opacity:1" />
        </linearGradient>
    </defs>

    <!-- Background -->
    <rect x="57" y="75" width="640" height="910" fill="#f8f9fa" class="map-background"/>

    <!-- Border Regions based on actual geography:
         - Belgium: West side only (left strip)
         - Germany: East side + Northeast corner (starts from top)
         - France: South side only (bottom strip)
         Note: Regions extend deeply under cantons to completely eliminate any white gaps
    -->
    <g class="border-regions" aria-label="{% trans 'Border regions' %}">
        <!-- Belgium - West side only (left strip) -->
        <path id="border-belgium"
              data-region-id="border-belgium"
              data-region-name="{% trans 'Belgium (Arlon area)' %}"
              role="option"
              aria-selected="false"
              d="M 57 75 L 350 75 L 350 985 L 57 985 Z"
              class="border-region border-belgium"/>

        <!-- Germany - East side + top right (right strip from top to bottom) -->
        <path id="border-germany"
              data-region-id="border-germany"
              data-region-name="{% trans 'Germany (Trier/Saarland area)' %}"
              role="option"
              aria-selected="false"
              d="M 350 75 L 697 75 L 697 985 L 350 985 Z"
              class="border-region border-germany"/>

        <!-- France - South side only (narrow bottom strip, only touching south of Luxembourg) -->
        <path id="border-france"
              data-region-id="border-france"
              data-region-name="{% trans 'France (Thionville/Metz area)' %}"
              role="option"
              aria-selected="false"
              d="M 57 850 L 697 850 L 697 985 L 57 985 Z"
              class="border-region border-france"/>
    </g>

    <!-- Luxembourg Cantons (rendered ON TOP of border regions) -->
    <g class="lux-cantons" aria-label="{% trans 'Luxembourg cantons' %}">
''')

    # Add each canton path
    for i, (fill, path_d) in enumerate(paths, 1):
        if i in CANTON_MAPPING:
            canton_id, canton_name = CANTON_MAPPING[i]
            # Clean up the path data (remove newlines and extra spaces)
            clean_path = ' '.join(path_d.split())

            output_lines.append(f'''        <!-- {canton_name} -->
        <path id="{canton_id}"
              data-region-id="{canton_id}"
              data-region-name="{canton_name}"
              role="option"
              aria-selected="false"
              d="{clean_path}"
              class="lux-canton"/>
''')

    output_lines.append('''    </g>

    <!-- Country labels positioned according to actual geography -->
    <g class="country-labels" aria-hidden="true">
        <!-- Belgium label on west side -->
        <text x="100" y="140" class="country-label">BELGIUM</text>
        <!-- Germany label on east side -->
        <text x="550" y="300" class="country-label">GERMANY</text>
        <!-- France label in narrow south strip -->
        <text x="150" y="960" class="country-label">FRANCE</text>
    </g>
</svg>
''')

    return '\n'.join(output_lines)


def main():
    paths = extract_canton_paths()
    if not paths:
        return

    # Generate the template
    template_content = generate_canton_svg_template(paths)

    # Save to the template file
    output_path = Path(__file__).parent.parent / "crush_lu" / "templates" / "crush_lu" / "partials" / "canton_map_svg.html"
    output_path.write_text(template_content, encoding='utf-8')

    print(f"\nGenerated canton map template: {output_path}")
    print(f"Template size: {len(template_content)} bytes")


if __name__ == "__main__":
    main()
