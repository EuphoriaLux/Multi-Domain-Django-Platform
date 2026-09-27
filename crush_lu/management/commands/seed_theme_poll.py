"""
Create the public theme-night ballot (``/<lang>/themes/``).

Idempotent: the poll is matched on its English title and each option on its
English name, so re-running only adds what is missing and never touches
votes, edits made in the admin, or the publish switch. The poll is created
unpublished; a coach publishes it in the admin after checking the list.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crush_lu.models.event_polls import EventPoll, EventPollOption

POLL = {
    "title": {
        "en": "Which theme night should we throw next?",
        "de": "Welchen Themenabend sollen wir als Nächstes machen?",
        "fr": "Quelle soirée à thème organiser ensuite ?",
    },
    "description": {
        "en": "Vote for every theme you would come to. The most wanted "
        "nights make it onto the Crush.lu calendar.",
        "de": "Stimm für jedes Thema, zu dem du kommen würdest. Die "
        "beliebtesten Abende landen im Crush.lu-Kalender.",
        "fr": "Votez pour chaque thème qui vous donnerait envie de venir. "
        "Les soirées les plus demandées rejoignent l'agenda Crush.lu.",
    },
}

# (icon, static_image, {lang: name}, {lang: description})
OPTIONS = [
    (
        "🐆",
        "",
        {
            "en": "Cougar & Toyboy Night",
            "de": "Cougar & Toyboy Night",
            "fr": "Soirée Cougar & Toyboy",
        },
        {
            "en": "Confident women 40+ meet younger men who like it that way.",
            "de": "Selbstbewusste Frauen ab 40 treffen jüngere Männer, die genau das mögen.",
            "fr": "Des femmes de 40 ans et plus rencontrent des hommes plus jeunes qui aiment ça.",
        },
    ),
    (
        "🦊",
        "",
        {
            "en": "Silver Fox Night",
            "de": "Silver Fox Night",
            "fr": "Soirée Silver Fox",
        },
        {
            "en": "Distinguished men 40+ meet younger women who like it that way.",
            "de": "Gestandene Männer ab 40 treffen jüngere Frauen, die genau das mögen.",
            "fr": "Des hommes de 40 ans et plus rencontrent des femmes plus jeunes qui aiment ça.",
        },
    ),
    (
        "⏳",
        "",
        {
            "en": "Age Is Just a Number",
            "de": "Alter ist nur eine Zahl",
            "fr": "L'âge n'est qu'un chiffre",
        },
        {
            "en": "Age-gap dating in both directions: 40+ meets under 35.",
            "de": "Altersunterschied in beide Richtungen: 40+ trifft unter 35.",
            "fr": "L'écart d'âge dans les deux sens : les 40+ rencontrent les moins de 35 ans.",
        },
    ),
    (
        "🪄",
        "",
        {
            "en": "Harry Potter Night",
            "de": "Harry-Potter-Abend",
            "fr": "Soirée Harry Potter",
        },
        {
            "en": "House sorting, wizard quiz and butterbeer.",
            "de": "Häuserauswahl, Zauberer-Quiz und Butterbier.",
            "fr": "Répartition dans les maisons, quiz de sorciers et bièraubeurre.",
        },
    ),
    (
        "🌈",
        "",
        {
            "en": "Rainbow Night (LGBTQ+)",
            "de": "Rainbow Night (LGBTQ+)",
            "fr": "Rainbow Night (LGBTQ+)",
        },
        {
            "en": "A dating night for the LGBTQ+ community and friends.",
            "de": "Ein Dating-Abend für die LGBTQ+-Community und Freunde.",
            "fr": "Une soirée rencontres pour la communauté LGBTQ+ et ses ami·es.",
        },
    ),
    (
        "📼",
        "",
        {
            "en": "80s/90s Throwback",
            "de": "80er/90er-Party",
            "fr": "Soirée années 80/90",
        },
        {
            "en": "Retro outfits, cassette-era hits and nostalgia.",
            "de": "Retro-Outfits, Kassetten-Hits und Nostalgie.",
            "fr": "Tenues rétro, tubes de l'époque des cassettes et nostalgie.",
        },
    ),
    (
        "🎧",
        "",
        {
            "en": "Silent Disco Dating",
            "de": "Silent-Disco-Dating",
            "fr": "Silent Disco Dating",
        },
        {
            "en": "Three channels, one headset: find who's dancing to your song.",
            "de": "Drei Kanäle, ein Kopfhörer: Finde, wer zu deinem Song tanzt.",
            "fr": "Trois canaux, un casque : trouvez qui danse sur votre chanson.",
        },
    ),
    (
        "🎲",
        "",
        {
            "en": "Board-Game Date Night",
            "de": "Brettspiel-Dating",
            "fr": "Soirée jeux de société",
        },
        {
            "en": "Rotating tables of quick games. Easy talk, no pressure.",
            "de": "Wechselnde Tische mit schnellen Spielen. Lockere Gespräche, kein Druck.",
            "fr": "Des tables tournantes de jeux rapides. On discute facilement, sans pression.",
        },
    ),
    (
        "🍷",
        "wine_tasting.png",
        {
            "en": "Wine & Cheese Tasting",
            "de": "Wein & Käse Tasting",
            "fr": "Dégustation vins & fromages",
        },
        {
            "en": "Local Moselle wines with a cheese pairing.",
            "de": "Lokale Moselweine mit passendem Käse.",
            "fr": "Vins locaux de la Moselle accompagnés de fromages.",
        },
    ),
    (
        "🎤",
        "",
        {
            "en": "Karaoke Crush",
            "de": "Karaoke Crush",
            "fr": "Karaoké Crush",
        },
        {
            "en": "Duets with strangers. Courage optional, fun guaranteed.",
            "de": "Duette mit Fremden. Mut optional, Spaß garantiert.",
            "fr": "Des duos avec des inconnus. Courage facultatif, fous rires garantis.",
        },
    ),
    (
        "🕶️",
        "",
        {
            "en": "Dinner in the Dark",
            "de": "Dinner im Dunkeln",
            "fr": "Dîner dans le noir",
        },
        {
            "en": "A blind date, literally: first impressions without looks.",
            "de": "Ein Blind Date im wörtlichen Sinn: erster Eindruck ohne Aussehen.",
            "fr": "Un rendez-vous à l'aveugle, au sens propre : une première impression sans le physique.",
        },
    ),
    (
        "🥾",
        "hiking.png",
        {
            "en": "Hike & Mingle",
            "de": "Wandern & Kennenlernen",
            "fr": "Randonnée & rencontres",
        },
        {
            "en": "A Mullerthal trail walk that ends at a terrace.",
            "de": "Eine Wanderung im Müllerthal, die auf einer Terrasse endet.",
            "fr": "Une balade dans le Mullerthal qui se termine en terrasse.",
        },
    ),
    (
        "👩‍🍳",
        "cookingworkshop.png",
        {
            "en": "Cooking Class Date",
            "de": "Koch-Date",
            "fr": "Atelier cuisine en duo",
        },
        {
            "en": "Cook in pairs, then eat what you made together.",
            "de": "Kocht zu zweit und esst dann zusammen, was ihr gemacht habt.",
            "fr": "Cuisinez en binôme, puis dégustez ensemble ce que vous avez préparé.",
        },
    ),
    (
        "🎭",
        "",
        {
            "en": "Masquerade Ball",
            "de": "Maskenball",
            "fr": "Bal masqué",
        },
        {
            "en": "Masks on, mystery up. Unmasking at midnight.",
            "de": "Masken auf, Spannung rauf. Demaskierung um Mitternacht.",
            "fr": "Masques sur le visage, mystère garanti. On se démasque à minuit.",
        },
    ),
    (
        "📚",
        "",
        {
            "en": "Book-Lovers Night",
            "de": "Abend für Bücherfans",
            "fr": "Soirée des amoureux des livres",
        },
        {
            "en": "Bring your favourite book and swap it for a conversation.",
            "de": "Bring dein Lieblingsbuch mit und tausche es gegen ein Gespräch.",
            "fr": "Apportez votre livre préféré et échangez-le contre une conversation.",
        },
    ),
]

LANGS = ("en", "de", "fr")


def _translated(field, values):
    return {f"{field}_{lang}": values[lang] for lang in LANGS}


class Command(BaseCommand):
    help = "Create the public theme-night ballot (unpublished) and its options."

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        poll = EventPoll.objects.filter(title_en=POLL["title"]["en"]).first()
        if poll is None:
            poll = EventPoll.objects.create(
                **_translated("title", POLL["title"]),
                **_translated("description", POLL["description"]),
                start_date=now,
                end_date=now + timedelta(days=183),
                is_published=False,
                is_public=True,
                allow_multiple_choices=True,
                show_results_before_close=True,
            )
            self.stdout.write(f"Created poll #{poll.pk} (unpublished).")
        else:
            self.stdout.write(f"Poll #{poll.pk} already exists.")

        existing = set(poll.options.values_list("name_en", flat=True))
        created = 0
        for order, (icon, image, names, descriptions) in enumerate(OPTIONS, 1):
            if names["en"] in existing:
                continue
            EventPollOption.objects.create(
                poll=poll,
                icon=icon,
                static_image=image,
                sort_order=order,
                **_translated("name", names),
                **_translated("description", descriptions),
            )
            created += 1
        self.stdout.write(
            self.style.SUCCESS(f"{created} option(s) added, {len(existing)} kept.")
        )
