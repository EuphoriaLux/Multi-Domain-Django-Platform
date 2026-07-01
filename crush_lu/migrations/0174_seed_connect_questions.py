"""
Seed the ``ConnectQuestion`` catalogue for "Read-the-Photo" matching (M8).

~32 owner-POV yes/no questions across categories, each with EN/DE/FR. Mild &
medium ship active; a small set of spicy questions (e.g. "body count") ships
INACTIVE so the coach team can promote a themed "spicy week" when ready.
Idempotent via ``update_or_create(slug=...)`` so re-running or editing here is safe.
"""

from django.db import migrations


# (slug, category, tier, weight, is_active, en, de, fr)
QUESTIONS = [
    # --- career & money ---
    ("work-finance", "career", 1, 3, True,
     "Do I work in finance?",
     "Arbeite ich im Finanzwesen?",
     "Est-ce que je travaille dans la finance ?"),
    ("creative-job", "career", 1, 2, True,
     "Do I have a creative job?",
     "Habe ich einen kreativen Beruf?",
     "Est-ce que j'ai un métier créatif ?"),
    ("own-business", "career", 2, 2, True,
     "Do I run my own business?",
     "Führe ich mein eigenes Unternehmen?",
     "Est-ce que je dirige ma propre entreprise ?"),
    ("suit-to-work", "career", 1, 1, True,
     "Do I wear a suit to work?",
     "Trage ich einen Anzug zur Arbeit?",
     "Est-ce que je porte un costume au travail ?"),
    ("saver", "career", 2, 1, True,
     "Am I more of a saver than a spender?",
     "Bin ich eher sparsam als spendabel?",
     "Suis-je plutôt économe que dépensier ?"),
    # --- lifestyle ---
    ("gym-regular", "lifestyle", 1, 3, True,
     "Do I go to the gym every week?",
     "Gehe ich jede Woche ins Fitnessstudio?",
     "Est-ce que je vais à la salle chaque semaine ?"),
    ("dog-person", "lifestyle", 1, 3, True,
     "Am I a dog person?",
     "Bin ich ein Hundemensch?",
     "Suis-je plutôt chien ?"),
    ("cat-person", "lifestyle", 1, 2, True,
     "Am I a cat person?",
     "Bin ich ein Katzenmensch?",
     "Suis-je plutôt chat ?"),
    ("early-riser", "lifestyle", 1, 2, True,
     "Am I an early riser?",
     "Bin ich ein Frühaufsteher?",
     "Suis-je du matin ?"),
    ("homebody", "lifestyle", 1, 2, True,
     "Am I more of a homebody than a party-goer?",
     "Bin ich eher ein Stubenhocker als ein Partygänger?",
     "Suis-je plutôt casanier que fêtard ?"),
    ("coffee-addict", "lifestyle", 1, 2, True,
     "Can I not function without my morning coffee?",
     "Komme ich ohne meinen Morgenkaffee nicht klar?",
     "Suis-je incapable de fonctionner sans mon café du matin ?"),
    ("well-travelled", "lifestyle", 2, 2, True,
     "Have I visited more than ten countries?",
     "Habe ich mehr als zehn Länder bereist?",
     "Ai-je visité plus de dix pays ?"),
    ("home-cook", "lifestyle", 1, 1, True,
     "Do I cook most of my meals?",
     "Koche ich die meisten meiner Mahlzeiten selbst?",
     "Est-ce que je cuisine la plupart de mes repas ?"),
    ("has-tattoo", "lifestyle", 2, 2, True,
     "Do I have a tattoo?",
     "Habe ich ein Tattoo?",
     "Est-ce que j'ai un tatouage ?"),
    ("plant-parent", "lifestyle", 1, 1, True,
     "Do I keep my houseplants alive?",
     "Halte ich meine Zimmerpflanzen am Leben?",
     "Est-ce que je garde mes plantes en vie ?"),
    # --- personality ---
    ("funnier", "personality", 1, 3, True,
     "Am I funnier than you?",
     "Bin ich lustiger als du?",
     "Suis-je plus drôle que toi ?"),
    ("adventurous", "personality", 1, 3, True,
     "Would I go skydiving?",
     "Würde ich Fallschirmspringen gehen?",
     "Est-ce que je sauterais en parachute ?"),
    ("introvert", "personality", 1, 2, True,
     "Am I more introvert than extrovert?",
     "Bin ich eher introvertiert als extrovertiert?",
     "Suis-je plutôt introverti qu'extraverti ?"),
    ("competitive", "personality", 1, 2, True,
     "Am I very competitive?",
     "Bin ich sehr wettbewerbsorientiert?",
     "Suis-je très compétitif ?"),
    ("romantic", "personality", 1, 2, True,
     "Am I a hopeless romantic?",
     "Bin ich ein hoffnungsloser Romantiker?",
     "Suis-je un romantique incorrigible ?"),
    ("planner", "personality", 1, 2, True,
     "Am I a planner rather than spontaneous?",
     "Bin ich eher ein Planer als spontan?",
     "Suis-je plutôt organisé que spontané ?"),
    ("optimist", "personality", 1, 1, True,
     "Am I an optimist?",
     "Bin ich ein Optimist?",
     "Suis-je optimiste ?"),
    ("talks-more", "personality", 2, 1, True,
     "Do I talk more than I listen?",
     "Rede ich mehr, als ich zuhöre?",
     "Est-ce que je parle plus que j'écoute ?"),
    # --- dating & romance ---
    ("text-first", "dating", 1, 3, True,
     "Would I text first after a great date?",
     "Würde ich nach einem tollen Date zuerst schreiben?",
     "Est-ce que j'écrirais en premier après un super rendez-vous ?"),
    ("pineapple-pizza", "dating", 1, 2, True,
     "Do I think pineapple belongs on pizza?",
     "Finde ich, dass Ananas auf Pizza gehört?",
     "Est-ce que je pense que l'ananas a sa place sur une pizza ?"),
    ("cook-first-date", "dating", 1, 1, True,
     "Would I cook on a first date?",
     "Würde ich beim ersten Date kochen?",
     "Est-ce que je cuisinerais lors d'un premier rendez-vous ?"),
    ("love-at-first-sight", "dating", 1, 2, True,
     "Do I believe in love at first sight?",
     "Glaube ich an Liebe auf den ersten Blick?",
     "Est-ce que je crois au coup de foudre ?"),
    ("friends-with-exes", "dating", 2, 1, True,
     "Am I still friends with my exes?",
     "Bin ich noch mit meinen Ex-Partnern befreundet?",
     "Suis-je encore ami avec mes ex ?"),
    ("long-relationship", "dating", 2, 1, True,
     "Have I been in a relationship longer than three years?",
     "War ich schon länger als drei Jahre in einer Beziehung?",
     "Ai-je déjà eu une relation de plus de trois ans ?"),
    ("pda-fan", "dating", 2, 1, True,
     "Am I into public displays of affection?",
     "Mag ich öffentliche Zärtlichkeiten?",
     "Est-ce que j'aime les démonstrations d'affection en public ?"),
    # --- spicy (seeded INACTIVE — promote into a themed week when ready) ---
    ("body-count-higher", "spicy", 3, 2, False,
     "Do I have a higher body count than you?",
     "Habe ich mehr Sexpartner gehabt als du?",
     "Ai-je eu plus de partenaires que toi ?"),
    ("one-night-stand", "spicy", 3, 1, False,
     "Have I had a one-night stand?",
     "Hatte ich schon einen One-Night-Stand?",
     "Ai-je déjà eu une aventure d'un soir ?"),
    ("big-flirt", "spicy", 3, 1, False,
     "Am I a big flirt?",
     "Bin ich ein großer Flirt?",
     "Suis-je un grand séducteur ?"),
]


def seed_questions(apps, schema_editor):
    ConnectQuestion = apps.get_model("crush_lu", "ConnectQuestion")
    for slug, category, tier, weight, is_active, en, de, fr in QUESTIONS:
        ConnectQuestion.objects.update_or_create(
            slug=slug,
            defaults={
                "category": category,
                "tier": tier,
                "weight": weight,
                "is_active": is_active,
                "text": en,
                "text_en": en,
                "text_de": de,
                "text_fr": fr,
            },
        )


def unseed_questions(apps, schema_editor):
    ConnectQuestion = apps.get_model("crush_lu", "ConnectQuestion")
    ConnectQuestion.objects.filter(slug__in=[row[0] for row in QUESTIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0173_connect_questions_schema"),
    ]

    operations = [
        migrations.RunPython(seed_questions, unseed_questions),
    ]
