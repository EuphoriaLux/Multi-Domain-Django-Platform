"""
Data migration to seed 40 personality traits (20 qualities + 20 defects)
with trilingual labels (EN/DE/FR) and 14 opposite pairs.

Data sourced from crush-matching-plan.docx.
"""

from django.db import migrations


QUALITIES = [
    {"slug": "patient", "label": "Patient", "label_de": "Geduldig", "label_fr": "Patient(e)", "category": "emotional", "sort_order": 1},
    {"slug": "funny", "label": "Funny", "label_de": "Humorvoll", "label_fr": "Drôle", "category": "social", "sort_order": 2},
    {"slug": "ambitious", "label": "Ambitious", "label_de": "Ehrgeizig", "label_fr": "Ambitieux(se)", "category": "mindset", "sort_order": 3},
    {"slug": "generous", "label": "Generous", "label_de": "Großzügig", "label_fr": "Généreux(se)", "category": "relational", "sort_order": 4},
    {"slug": "adventurous", "label": "Adventurous", "label_de": "Abenteuerlustig", "label_fr": "Aventurier(ère)", "category": "energy", "sort_order": 5},
    {"slug": "creative", "label": "Creative", "label_de": "Kreativ", "label_fr": "Créatif(ve)", "category": "mindset", "sort_order": 6},
    {"slug": "independent", "label": "Independent", "label_de": "Unabhängig", "label_fr": "Indépendant(e)", "category": "energy", "sort_order": 7},
    {"slug": "kindhearted", "label": "Kind-hearted", "label_de": "Warmherzig", "label_fr": "Bienveillant(e)", "category": "emotional", "sort_order": 8},
    {"slug": "curious", "label": "Curious", "label_de": "Neugierig", "label_fr": "Curieux(se)", "category": "mindset", "sort_order": 9},
    {"slug": "loyal", "label": "Loyal", "label_de": "Treu", "label_fr": "Loyal(e)", "category": "relational", "sort_order": 10},
    {"slug": "calm", "label": "Calm", "label_de": "Ruhig", "label_fr": "Calme", "category": "emotional", "sort_order": 11},
    {"slug": "optimistic", "label": "Optimistic", "label_de": "Optimistisch", "label_fr": "Optimiste", "category": "mindset", "sort_order": 12},
    {"slug": "sociable", "label": "Sociable", "label_de": "Gesellig", "label_fr": "Sociable", "category": "social", "sort_order": 13},
    {"slug": "honest", "label": "Honest", "label_de": "Ehrlich", "label_fr": "Honnête", "category": "relational", "sort_order": 14},
    {"slug": "caring", "label": "Caring", "label_de": "Fürsorglich", "label_fr": "Attentionné(e)", "category": "relational", "sort_order": 15},
    {"slug": "organized", "label": "Organized", "label_de": "Organisiert", "label_fr": "Organisé(e)", "category": "energy", "sort_order": 16},
    {"slug": "spontaneous", "label": "Spontaneous", "label_de": "Spontan", "label_fr": "Spontané(e)", "category": "energy", "sort_order": 17},
    {"slug": "empathetic", "label": "Empathetic", "label_de": "Einfühlsam", "label_fr": "Empathique", "category": "emotional", "sort_order": 18},
    {"slug": "confident", "label": "Confident", "label_de": "Selbstbewusst", "label_fr": "Confiant(e)", "category": "social", "sort_order": 19},
    {"slug": "romantic", "label": "Romantic", "label_de": "Romantisch", "label_fr": "Romantique", "category": "relational", "sort_order": 20},
]

DEFECTS = [
    {"slug": "stubborn", "label": "Stubborn", "label_de": "Sturköpfig", "label_fr": "Têtu(e)", "category": "mindset", "sort_order": 1},
    {"slug": "messy", "label": "Messy", "label_de": "Unordentlich", "label_fr": "Désordonné(e)", "category": "energy", "sort_order": 2},
    {"slug": "impatient", "label": "Impatient", "label_de": "Ungeduldig", "label_fr": "Impatient(e)", "category": "emotional", "sort_order": 3},
    {"slug": "jealous", "label": "Jealous", "label_de": "Eifersüchtig", "label_fr": "Jaloux(se)", "category": "relational", "sort_order": 4},
    {"slug": "absentminded", "label": "Absent-minded", "label_de": "Zerstreut", "label_fr": "Distrait(e)", "category": "mindset", "sort_order": 5},
    {"slug": "perfectionist", "label": "Perfectionist", "label_de": "Perfektionistisch", "label_fr": "Perfectionniste", "category": "mindset", "sort_order": 6},
    {"slug": "anxious", "label": "Anxious", "label_de": "Ängstlich", "label_fr": "Anxieux(se)", "category": "emotional", "sort_order": 7},
    {"slug": "sensitive", "label": "Sensitive", "label_de": "Empfindlich", "label_fr": "Susceptible", "category": "emotional", "sort_order": 8},
    {"slug": "homebody", "label": "Homebody", "label_de": "Häuslich", "label_fr": "Casanier(ère)", "category": "energy", "sort_order": 9},
    {"slug": "talkative", "label": "Talkative", "label_de": "Redelig", "label_fr": "Bavard(e)", "category": "social", "sort_order": 10},
    {"slug": "reserved", "label": "Reserved", "label_de": "Zurückhaltend", "label_fr": "Réservé(e)", "category": "social", "sort_order": 11},
    {"slug": "indecisive", "label": "Indecisive", "label_de": "Unentschlossen", "label_fr": "Indécis(e)", "category": "mindset", "sort_order": 12},
    {"slug": "sarcastic", "label": "Sarcastic", "label_de": "Sarkastisch", "label_fr": "Sarcastique", "category": "social", "sort_order": 13},
    {"slug": "workaholic", "label": "Workaholic", "label_de": "Workaholic", "label_fr": "Workaholic", "category": "energy", "sort_order": 14},
    {"slug": "possessive", "label": "Possessive", "label_de": "Besitzergreifend", "label_fr": "Possessif(ve)", "category": "relational", "sort_order": 15},
    {"slug": "clumsy", "label": "Clumsy", "label_de": "Tollpatschig", "label_fr": "Maladroit(e)", "category": "social", "sort_order": 16},
    {"slug": "cynical", "label": "Cynical", "label_de": "Zynisch", "label_fr": "Cynique", "category": "mindset", "sort_order": 17},
    {"slug": "spender", "label": "Spender", "label_de": "Verschwenderisch", "label_fr": "Dépensier(ère)", "category": "energy", "sort_order": 18},
    {"slug": "bossy", "label": "Bossy", "label_de": "Dominant", "label_fr": "Autoritaire", "category": "relational", "sort_order": 19},
    {"slug": "late", "label": "Always late", "label_de": "Unpünktlich", "label_fr": "Retardataire", "category": "energy", "sort_order": 20},
]

# 14 opposite pairs: quality_slug -> defect_slug
OPPOSITE_PAIRS = [
    ("patient", "impatient"),
    ("adventurous", "homebody"),
    ("organized", "messy"),
    ("calm", "anxious"),
    ("sociable", "reserved"),
    ("confident", "indecisive"),
    ("spontaneous", "perfectionist"),
    ("generous", "possessive"),
    ("honest", "sarcastic"),
    ("caring", "cynical"),
    ("independent", "jealous"),
    ("optimistic", "sensitive"),
    ("creative", "stubborn"),
    ("ambitious", "workaholic"),
]


def seed_traits(apps, schema_editor):
    Trait = apps.get_model("crush_lu", "Trait")

    # Create qualities
    for data in QUALITIES:
        Trait.objects.get_or_create(
            slug=data["slug"],
            defaults={
                "label": data["label"],
                "label_en": data["label"],
                "label_de": data["label_de"],
                "label_fr": data["label_fr"],
                "trait_type": "quality",
                "category": data["category"],
                "sort_order": data["sort_order"],
            },
        )

    # Create defects
    for data in DEFECTS:
        Trait.objects.get_or_create(
            slug=data["slug"],
            defaults={
                "label": data["label"],
                "label_en": data["label"],
                "label_de": data["label_de"],
                "label_fr": data["label_fr"],
                "trait_type": "defect",
                "category": data["category"],
                "sort_order": data["sort_order"],
            },
        )

    # Link opposite pairs (bidirectional)
    for quality_slug, defect_slug in OPPOSITE_PAIRS:
        try:
            quality = Trait.objects.get(slug=quality_slug)
            defect = Trait.objects.get(slug=defect_slug)
            quality.opposite = defect
            quality.save(update_fields=["opposite"])
            defect.opposite = quality
            defect.save(update_fields=["opposite"])
        except Trait.DoesNotExist:
            pass


def unseed_traits(apps, schema_editor):
    Trait = apps.get_model("crush_lu", "Trait")
    slugs = [q["slug"] for q in QUALITIES] + [d["slug"] for d in DEFECTS]
    Trait.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0105_matching_system"),
    ]

    operations = [
        migrations.RunPython(seed_traits, unseed_traits),
    ]
