"""
Seed the curated ``ConnectInterest`` catalogue (mirrors 0106_seed_traits).

~38 interests across the 8 categories, each with EN/DE/FR labels. Idempotent
via ``update_or_create(slug=...)`` so re-running (or editing labels here later)
is safe.
"""

from django.db import migrations


# (slug, category, en, de, fr)
INTERESTS = [
    # --- sports ---
    ("football", "sports", "Football", "Fußball", "Football"),
    ("running", "sports", "Running", "Laufen", "Course à pied"),
    ("cycling", "sports", "Cycling", "Radfahren", "Vélo"),
    ("swimming", "sports", "Swimming", "Schwimmen", "Natation"),
    ("tennis", "sports", "Tennis", "Tennis", "Tennis"),
    ("fitness", "sports", "Fitness & gym", "Fitness & Gym", "Fitness & musculation"),
    # --- music ---
    ("live-music", "music", "Live music", "Live-Musik", "Concerts"),
    ("electronic", "music", "Electronic", "Elektronische Musik", "Musique électronique"),
    ("classical", "music", "Classical", "Klassik", "Musique classique"),
    ("instruments", "music", "Playing an instrument", "Instrument spielen", "Jouer d'un instrument"),
    ("singing", "music", "Singing", "Singen", "Chant"),
    # --- travel ---
    ("city-trips", "travel", "City trips", "Städtereisen", "City-trips"),
    ("backpacking", "travel", "Backpacking", "Backpacking", "Voyage sac à dos"),
    ("beach-holidays", "travel", "Beach holidays", "Strandurlaub", "Vacances à la plage"),
    ("road-trips", "travel", "Road trips", "Roadtrips", "Road trips"),
    # --- food ---
    ("cooking", "food", "Cooking", "Kochen", "Cuisine"),
    ("dining-out", "food", "Dining out", "Essen gehen", "Restaurants"),
    ("wine", "food", "Wine", "Wein", "Vin"),
    ("coffee", "food", "Coffee", "Kaffee", "Café"),
    ("baking", "food", "Baking", "Backen", "Pâtisserie"),
    # --- arts ---
    ("cinema", "arts", "Cinema", "Kino", "Cinéma"),
    ("reading", "arts", "Reading", "Lesen", "Lecture"),
    ("museums", "arts", "Museums", "Museen", "Musées"),
    ("photography", "arts", "Photography", "Fotografie", "Photographie"),
    ("theatre", "arts", "Theatre", "Theater", "Théâtre"),
    ("painting", "arts", "Painting & drawing", "Malen & Zeichnen", "Peinture & dessin"),
    # --- outdoors ---
    ("hiking", "outdoors", "Hiking", "Wandern", "Randonnée"),
    ("camping", "outdoors", "Camping", "Camping", "Camping"),
    ("gardening", "outdoors", "Gardening", "Gärtnern", "Jardinage"),
    ("climbing", "outdoors", "Climbing", "Klettern", "Escalade"),
    # --- games ---
    ("board-games", "games", "Board games", "Brettspiele", "Jeux de société"),
    ("video-games", "games", "Video games", "Videospiele", "Jeux vidéo"),
    ("chess", "games", "Chess", "Schach", "Échecs"),
    ("esports", "games", "Esports", "E-Sport", "Esport"),
    # --- wellness ---
    ("yoga", "wellness", "Yoga", "Yoga", "Yoga"),
    ("meditation", "wellness", "Meditation", "Meditation", "Méditation"),
    ("spa", "wellness", "Spa & wellness", "Spa & Wellness", "Spa & bien-être"),
    ("mindfulness", "wellness", "Mindfulness", "Achtsamkeit", "Pleine conscience"),
]


def seed_interests(apps, schema_editor):
    ConnectInterest = apps.get_model("crush_lu", "ConnectInterest")
    per_category_order = {}
    for slug, category, en, de, fr in INTERESTS:
        order = per_category_order.get(category, 0)
        per_category_order[category] = order + 1
        ConnectInterest.objects.update_or_create(
            slug=slug,
            defaults={
                "category": category,
                "sort_order": order,
                "is_active": True,
                "label": en,
                "label_en": en,
                "label_de": de,
                "label_fr": fr,
            },
        )


def unseed_interests(apps, schema_editor):
    ConnectInterest = apps.get_model("crush_lu", "ConnectInterest")
    ConnectInterest.objects.filter(slug__in=[row[0] for row in INTERESTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0162_copy_connect_prefs"),
    ]

    operations = [
        migrations.RunPython(seed_interests, unseed_interests),
    ]
