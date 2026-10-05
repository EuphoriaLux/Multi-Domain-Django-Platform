"""
DE/FR coverage for Crush Connect consent, the Connect Week card button, the
Event Lobby safety path and the post-event "My Crush!" flow (CX review P4-4,
findings CC-02, CC-18, SD-04).

Before this change the pages below were English for German and French members:
the two Crush Connect opt-in checkboxes, the Connect Week "Answer" button, the
whole "Hide or remove this person" block of People I've Met, the Event Lobby
locked/closed pages and most of the "My Crush!" screens. The entries were
EMPTY in the ``.po`` files, or ``#, fuzzy`` with a msgstr that belonged to a
different message (e.g. "Quiz für dieses Event erstellt." for "Crush declared
for this event"), which Django never ships, so members saw English.

Rules these tests follow (see AGENTS.md "Traps"):
- literal ``/en|de|fr/...`` paths with ``HTTP_HOST="crush.lu"`` (``reverse()``
  would build ``/crush/...`` paths that 404 under the host routing);
- ``cache.clear()`` before every test (shared ``@ratelimit`` counters, PKs
  restart on SQLite);
- the DE/FR copy is spelled out here on purpose, so an accidental edit of the
  ``.po`` files fails a test instead of silently changing consent wording.

The consent checkboxes are asserted against a FAITHFUL translation of the
current English ("matched ..."). The English wording itself is under review
(CC-04) and is deliberately not changed here.
"""

import html
import re
from collections import Counter
from pathlib import Path

import polib
import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import translation

from crush_lu.models import (
    ConfirmedEncounterRemovalRequest,
    EventConnection,
    UserBlock,
)
from crush_lu.models.crush_connect_cycle import ConnectCycleCard
from crush_lu.services.connect_cycle import CARDS_PER_DAY
from crush_lu.tests.test_connect_week_experience import (
    _make_cycle_user,
    _seed_cycle_pool,
)
from crush_lu.tests.test_crush_connect import (
    _login_eligible,
    _make_user,
    _mark_attended,
)
from crush_lu.tests.test_crush_connect_onboarding import (
    _complete_steps,
    _week_question_ids,
)
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _login,
    _make_event,
    _make_member,
)
from crush_lu.tests.test_event_lobby_recap import _make_encounter

HOST = "crush.lu"
LANGS = ("de", "fr")
LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"


@pytest.fixture(autouse=True)
def _flags_and_cache(settings):
    cache.clear()
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.AZURE_ACCOUNT_NAME = ""  # local photo serving, no Azure calls
    yield
    cache.clear()


@pytest.fixture
def client():
    return Client(HTTP_HOST=HOST)


def page(response):
    """Response body with HTML entities decoded (form labels are autoescaped)."""
    return html.unescape(response.content.decode())


def assert_all(body, expected, lang):
    missing = [text for text in expected if text not in body]
    assert not missing, f"[{lang}] not rendered: {missing}"


def assert_none(body, unexpected, lang):
    present = [text for text in unexpected if text in body]
    assert not present, f"[{lang}] must not render: {present}"


# ---------------------------------------------------------------------------
# 1. Crush Connect onboarding step 7: the two required consent checkboxes
# ---------------------------------------------------------------------------

CONSENT_EN = (
    "I agree my clear photo is shown to the few people matched to me each day "
    "so they can guess my questions.",
    "I understand my photo, first name, age range and the 3 questions I chose "
    "appear on my card to the members matched with me, and I can be removed "
    "from Crush Connect at any time.",
)

CONSENT = {
    "de": (
        "Ich bin einverstanden, dass mein klares Foto den wenigen Personen, mit "
        "denen ich jeden Tag gematcht werde, gezeigt wird, damit sie meine "
        "Antworten erraten können.",
        "Ich verstehe, dass mein Foto, mein Vorname, meine Altersspanne und die "
        "3 von mir gewählten Fragen auf meiner Karte den Mitgliedern angezeigt "
        "werden, mit denen ich gematcht werde, und dass ich jederzeit aus "
        "Crush Connect entfernt werden kann.",
    ),
    "fr": (
        "J'accepte que ma photo nette soit montrée aux quelques personnes avec "
        "lesquelles je suis mis(e) en relation chaque jour, afin qu'elles "
        "puissent deviner mes réponses.",
        "Je comprends que ma photo, mon prénom, ma tranche d'âge et les 3 "
        "questions que j'ai choisies apparaissent sur ma carte pour les "
        "membres avec lesquels je suis mis(e) en relation, et que je peux être "
        "retiré(e) de Crush Connect à tout moment.",
    ),
}

# Wording of the retired blurred-photo card (stale fuzzy msgstr) and the
# interim "proposed to me" wording that changes the meaning of the English.
CONSENT_FORBIDDEN = {
    "de": ("unscharfem Foto", "meine Story", "vorgeschlagen"),
    "fr": ("photo floutée", "ma Story", "proposé"),
}


@pytest.fixture
def step7_member(client):
    me = _make_user(username="p44_step7", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)
    _complete_steps(client, 1, 6)
    return me


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_step7_consent_checkboxes_are_translated(client, step7_member, lang):
    response = client.get(f"/{lang}/crush-connect/onboarding/7/")

    assert response.status_code == 200
    body = page(response)
    assert_all(body, CONSENT[lang], lang)
    assert_none(body, CONSENT_EN, lang)
    assert_none(body, CONSENT_FORBIDDEN[lang], lang)
    # Negative control: both boxes are still REQUIRED, only their label changed.
    assert re.search(r'name="photo_share_consent"[^>]*\brequired\b', body)
    assert re.search(r'name="confirm_terms"[^>]*\brequired\b', body)


@pytest.mark.django_db
def test_step7_consent_english_is_unchanged(client, step7_member):
    body = page(client.get("/en/crush-connect/onboarding/7/"))

    assert_all(body, CONSENT_EN, "en")


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_step7_still_requires_both_consents_in_translation(client, step7_member, lang):
    data = {f"q_{qid}": "yes" for qid in _week_question_ids(3)}
    data["photo_share_consent"] = "on"  # confirm_terms missing

    response = client.post(f"/{lang}/crush-connect/onboarding/7/", data)

    assert response.status_code == 200
    step7_member.crush_connect_membership.refresh_from_db()
    assert step7_member.crush_connect_membership.onboarded_at is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lang", "expected"),
    (
        ("de", "Wähle genau 3 Fragen und beantworte jede über dich selbst."),
        (
            "fr",
            "Choisissez exactement 3 questions et répondez à chacune à votre sujet.",
        ),
    ),
)
def test_step7_validation_error_is_translated(client, step7_member, lang, expected):
    data = {f"q_{qid}": "yes" for qid in _week_question_ids(2)}  # only two picks
    data["photo_share_consent"] = "on"
    data["confirm_terms"] = "on"

    body = page(client.post(f"/{lang}/crush-connect/onboarding/7/", data))

    assert expected in body
    assert "Pick exactly" not in body


# ---------------------------------------------------------------------------
# 2. Connect Week card: submit button, "read" state and aria-label
# ---------------------------------------------------------------------------

WEEK = {
    "en": {
        "button": "Save my guesses",
        "read": "You've read {name} today.",
        "aria": "Connect Week card for {name}",
    },
    "de": {
        "button": "Tipps speichern",
        "read": "Du hast {name} heute gelesen.",
        "aria": "Connect-Week-Karte für {name}",
    },
    "fr": {
        "button": "Enregistrer mes suppositions",
        "read": "Vous avez lu {name} aujourd'hui.",
        "aria": "Carte Connect Week pour {name}",
    },
}


def _week_member(client):
    me = _make_cycle_user("p44_week")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)
    return me


def _submit_buttons(body):
    """Texts of the card submit buttons (``data-gate-submit``)."""
    found = re.findall(
        r"<button[^>]*data-gate-submit[^>]*>(.*?)</button>", body, flags=re.S
    )
    return [re.sub(r"<[^>]+>", "", text).strip() for text in found]


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ("en", "de", "fr"))
def test_week_card_button_and_aria_label_per_language(client, lang):
    me = _week_member(client)

    response = client.get(f"/{lang}/crush-connect/week/")

    assert response.status_code == 200
    body = page(response)
    buttons = _submit_buttons(body)
    assert len(buttons) == CARDS_PER_DAY
    assert set(buttons) == {WEEK[lang]["button"]}
    # The old label "Answer" invited readers to answer FOR the other person.
    assert "Answer" not in buttons
    for card in ConnectCycleCard.objects.filter(session__user=me):
        name = card.target_user.first_name
        assert f'aria-label="{WEEK[lang]["aria"].format(name=name)}"' in body


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ("en", "de", "fr"))
def test_answering_one_card_shows_read_state_and_keeps_the_other_button(client, lang):
    me = _week_member(client)
    client.get(f"/{lang}/crush-connect/week/")  # generates today's cards
    card = ConnectCycleCard.objects.filter(session__user=me).order_by("pk").first()
    membership = card.target_user.crush_connect_membership
    answers = {
        f"answer_{gq.question_id}": "yes" for gq in membership.active_gate_questions
    }

    response = client.post(
        f"/{lang}/crush-connect/week/card/{card.pk}/answer/", answers, follow=True
    )

    body = page(response)
    name = card.target_user.first_name
    assert WEEK[lang]["read"].format(name=name) in body
    if lang != "en":
        assert f"You've read {name} today." not in body
    # Negative control: the card that is still open keeps its submit button.
    assert _submit_buttons(body) == [WEEK[lang]["button"]] * (CARDS_PER_DAY - 1)
    card.refresh_from_db()
    assert card.is_completed is True


# ---------------------------------------------------------------------------
# 3. People I've Met: the "Hide or remove this person" safety path
# ---------------------------------------------------------------------------

LOBBY = {
    "en": {
        "met": "You met at a Crush.lu event.",
        "summary": "Hide or remove this person",
        "intro": (
            "They will disappear from People I've Met immediately. Your reason "
            "and details are private and reviewed only by Support."
        ),
        "select": "Select a reason",
        "reasons": (
            "I feel unsafe or uncomfortable",
            "Privacy concern",
            "We did not actually meet",
            "I no longer want this person visible",
            "Another reason",
        ),
        "details": "Private details (optional)",
        "placeholder": "Tell Support anything they should know.",
        "submit": "Hide person and request review",
        "record": (
            "A permanent record of a real-world meeting. Say hi next time you "
            "see them."
        ),
        "flash_ok": (
            "This person is now hidden. Support will review your private request."
        ),
        "flash_bad": "Please select a valid reason.",
        "empty_title": "No one here yet",
        "empty_body": (
            "When you and someone you met at an event both confirm it, they'll "
            "appear here."
        ),
    },
    "de": {
        "met": "Ihr habt euch bei einem Crush.lu-Event getroffen.",
        "summary": "Person ausblenden oder entfernen",
        "intro": (
            "Die Person verschwindet sofort aus „Meine Begegnungen“. Dein Grund "
            "und deine Angaben sind privat und werden nur vom Support geprüft."
        ),
        "select": "Grund auswählen",
        "reasons": (
            "Ich fühle mich unsicher oder unwohl",
            "Datenschutzbedenken",
            "Wir haben uns nicht wirklich getroffen",
            "Ich möchte diese Person nicht mehr sichtbar haben",
            "Anderer Grund",
        ),
        "details": "Private Angaben (optional)",
        "placeholder": "Sag dem Support alles, was er wissen sollte.",
        "submit": "Person ausblenden und Prüfung anfordern",
        "record": (
            "Ein bleibender Eintrag über eine echte Begegnung. Sag Hallo, wenn "
            "du die Person das nächste Mal siehst."
        ),
        "flash_ok": (
            "Die Person ist jetzt ausgeblendet. Der Support prüft deine private "
            "Anfrage."
        ),
        "flash_bad": "Bitte wähle einen gültigen Grund aus.",
        "empty_title": "Noch niemand hier",
        "empty_body": (
            "Wenn du und jemand, den du bei einem Event getroffen hast, es beide "
            "bestätigt, erscheint die Person hier."
        ),
    },
    "fr": {
        "met": "Vous vous êtes rencontrés lors d'un événement Crush.lu.",
        "summary": "Masquer ou retirer cette personne",
        "intro": (
            "Cette personne disparaît immédiatement de « Mes rencontres ». Votre "
            "motif et vos détails restent privés et ne sont examinés que par le "
            "support."
        ),
        "select": "Sélectionner un motif",
        "reasons": (
            "Je me sens en insécurité ou mal à l'aise",
            "Préoccupation liée à la vie privée",
            "Nous ne nous sommes pas réellement rencontrés",
            "Je ne souhaite plus que cette personne soit visible",
            "Autre motif",
        ),
        "details": "Détails privés (facultatif)",
        "placeholder": "Dites au support tout ce qu'il devrait savoir.",
        "submit": "Masquer la personne et demander un examen",
        "record": (
            "Une trace durable d'une vraie rencontre. Dites bonjour la prochaine "
            "fois que vous vous croiserez."
        ),
        "flash_ok": (
            "Cette personne est maintenant masquée. Le support examinera votre "
            "demande privée."
        ),
        "flash_bad": "Veuillez sélectionner un motif valide.",
        "empty_title": "Personne pour l'instant",
        "empty_body": (
            "Lorsque vous et une personne rencontrée lors d'un événement le "
            "confirmez tous les deux, elle apparaîtra ici."
        ),
    },
}

# Old fuzzy msgstrs that were wrong for these msgids (would have shipped had the
# fuzzy flag simply been removed).
LOBBY_STALE = {
    "de": (
        "Du musst der Nutzung der Crush.lu-Dienste zustimmen",
        "Datenschutzhinweis:",
        "Wann habt ihr euch zum ersten Mal getroffen?",
        "Notizen (optional)",
        "Bitte wähle einen Coach aus.",
        "Hier ist noch nichts.",
    ),
    "fr": (
        "Vous devez consentir à l'utilisation des services Crush.lu",
        "Confidentialité d'abord :",
        "Quand vous etes-vous rencontres",
        "Notes (facultatif)",
        "Veuillez sélectionner un coach.",
        "Rien ici pour le moment.",
    ),
}

PERSON_URL = "/{lang}/crush-connect/people-ive-met/{pk}/"
REMOVE_URL = "/{lang}/crush-connect/people-ive-met/{pk}/remove/"
PEOPLE_URL = "/{lang}/crush-connect/people-ive-met/"


def _encounter_pair():
    alice = _make_member("alice")
    ben = _make_member("ben", gender="M")
    encounter = _make_encounter(alice, ben)
    return alice, ben, encounter


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ("en", "de", "fr"))
def test_person_profile_safety_block_per_language(client, lang):
    alice, ben, _encounter = _encounter_pair()
    _login(client, alice)

    response = client.get(PERSON_URL.format(lang=lang, pk=ben.pk))

    assert response.status_code == 200
    body = page(response)
    expected = LOBBY[lang]
    assert_all(
        body,
        [
            expected["met"],
            expected["summary"],
            expected["intro"],
            expected["select"],
            expected["details"],
            expected["submit"],
            expected["record"],
            *expected["reasons"],
        ],
        lang,
    )
    assert f'placeholder="{expected["placeholder"]}"' in body
    if lang != "en":
        assert_none(body, LOBBY["en"]["reasons"], lang)
        assert_none(
            body,
            [
                LOBBY["en"]["met"],
                LOBBY["en"]["summary"],
                LOBBY["en"]["submit"],
                LOBBY["en"]["intro"],
            ],
            lang,
        )
        assert_none(body, LOBBY_STALE[lang], lang)
    # Negative control: the form still posts the machine values, only the
    # visible labels are translated.
    removal_select = re.search(
        r'<select id="removal-reason".*?</select>', body, flags=re.S
    ).group(0)
    reason_values = re.findall(r'<option value="([^"]*)"', removal_select)
    assert reason_values == [
        "",
        *(value for value, _label in ConfirmedEncounterRemovalRequest.REASON_CHOICES),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_removal_flow_flashes_and_empty_state_are_translated(client, lang):
    alice, ben, encounter = _encounter_pair()
    _login(client, alice)
    expected = LOBBY[lang]

    # The entry is listed while the encounter is active (negative control for
    # the empty state below).
    listed = page(client.get(PEOPLE_URL.format(lang=lang)))
    assert PERSON_URL.format(lang=lang, pk=ben.pk) in listed
    assert expected["empty_title"] not in listed

    invalid = client.post(
        REMOVE_URL.format(lang=lang, pk=ben.pk),
        {"reason": "not-a-reason", "details": "x"},
        follow=True,
    )
    assert expected["flash_bad"] in page(invalid)
    assert LOBBY["en"]["flash_bad"] not in page(invalid)
    assert not ConfirmedEncounterRemovalRequest.objects.exists()

    removed = client.post(
        REMOVE_URL.format(lang=lang, pk=ben.pk),
        {"reason": ConfirmedEncounterRemovalRequest.REASON_SAFETY, "details": "x"},
        follow=True,
    )

    body = page(removed)
    assert_all(
        body,
        [expected["flash_ok"], expected["empty_title"], expected["empty_body"]],
        lang,
    )
    assert_none(body, [LOBBY["en"]["flash_ok"], LOBBY["en"]["empty_title"]], lang)
    assert_none(body, LOBBY_STALE[lang], lang)
    # Behaviour unchanged: the removal still hides the encounter at once.
    encounter.refresh_from_db()
    assert encounter.status == "removal_pending"
    removal = ConfirmedEncounterRemovalRequest.objects.get()
    assert removal.requested_by_id == alice.pk
    assert removal.reason == ConfirmedEncounterRemovalRequest.REASON_SAFETY


LOBBY_PAGES = {
    "de": {
        "locked": (
            "In der Event-Lobby können eingecheckte Crush-Connect-Mitglieder "
            "diskret zeigen, wen sie heute Abend gern treffen würden. Schließe "
            "dein Crush-Connect-Profil vor Ende des Events ab, und du bist sofort "
            "dabei."
        ),
        "closed": (
            "Die Live-Lobby ist beendet",
            "Die Lobby dieses Events ist geschlossen.",
        ),
        "stale_closed": ("Die Event Lobby ist live",),
    },
    "fr": {
        "locked": (
            "Dans l'Event Lobby, les membres Crush Connect enregistrés peuvent "
            "indiquer discrètement qui ils aimeraient rencontrer ce soir. "
            "Terminez votre profil Crush Connect avant la fin de l'événement et "
            "vous y entrerez aussitôt."
        ),
        "closed": (
            "Le lobby en direct est terminé",
            "Le lobby de cet événement est fermé.",
        ),
        "stale_closed": ("L'Event Lobby est en direct",),
    },
}
LOBBY_PAGES_EN = {
    "locked": (
        "The Event Lobby is where checked-in Crush Connect members can quietly "
        "signal who they'd like to meet tonight. Complete your Crush Connect "
        "profile before the event ends and you'll join instantly."
    ),
    "closed": ("The live lobby has ended", "This event's lobby is closed."),
}


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ("en", "de", "fr"))
def test_event_lobby_locked_page_per_language(client, lang):
    event = _make_event()
    guest = _make_member("guest", onboarded=False)
    _attend(guest, event)
    _login(client, guest)

    response = client.get(f"/{lang}/events/{event.pk}/lobby/")

    assert response.status_code == 200
    body = page(response)
    expected = LOBBY_PAGES_EN["locked"] if lang == "en" else LOBBY_PAGES[lang]["locked"]
    assert expected in body
    if lang != "en":
        assert LOBBY_PAGES_EN["locked"] not in body


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ("en", "de", "fr"))
def test_event_lobby_closed_page_per_language(client, lang):
    event = _make_event()
    member = _make_member("memb")
    _attend(member, event)
    _end_event(event, hours_ago=60)  # past the 48h recap window
    _login(client, member)

    response = client.get(f"/{lang}/events/{event.pk}/lobby/")

    assert response.status_code == 200
    body = page(response)
    expected = LOBBY_PAGES_EN["closed"] if lang == "en" else LOBBY_PAGES[lang]["closed"]
    assert_all(body, expected, lang)
    if lang != "en":
        assert_none(body, LOBBY_PAGES_EN["closed"], lang)
        # the old fuzzy guess said the opposite ("The Event Lobby is live")
        assert_none(body, LOBBY_PAGES[lang]["stale_closed"], lang)


# ---------------------------------------------------------------------------
# 4. Post-event "My Crush!" flow
# ---------------------------------------------------------------------------

CRUSH = {
    "de": {
        "title": "Mein Crush! – So funktioniert's",
        "intro": (
            "Wer ist dir aufgefallen? Erkläre deinen Crush – das bleibt völlig "
            "privat, und die Person wird nie benachrichtigt. Dein Crush Coach "
            "ruft dich innerhalb von 48 Stunden an, hört deine Geschichte und "
            "arrangiert die Vorstellung persönlich."
        ),
        "allowance_title": "Ein Crush pro Event",
        "allowance": (
            "Du kannst bei diesem Event <strong>1</strong> Crush erklären. "
            "Überlege gut – dein Coach ruft dich deshalb an."
        ),
        "button": "Mein Crush!",
        "page_title": "Mein Crush! 💘",
        "form_heading": "Erzähle deinem Coach, was passiert ist (optional)",
        "private_note": "Das liest nur dein Crush Coach – niemals dein Crush.",
        "next_steps": (
            "Neugierig, wie das jede Woche funktioniert? Frag deinen Coach im "
            "Gespräch nach Crush Connect"
        ),
        "declared_flash": (
            "Crush erklärt 💕 Das bleibt völlig privat – die Person erfährt nie "
            "davon, es sei denn, dein Coach stellt euch vor. Dein Crush Coach "
            "ruft dich innerhalb von 48 Stunden an, um darüber zu sprechen."
        ),
        "used_title": "Crush für dieses Event erklärt",
        "used_body": (
            "Du hast deinen Crush für dieses Event genutzt. Dein Crush Coach "
            "ruft dich innerhalb von 48 Stunden an, um darüber zu sprechen."
        ),
        "card_state": "Crush erklärt – dein Coach ruft dich an",
        "duplicate_flash": "Du hast diese Person bereits als deinen Crush angegeben.",
        "limit_flash": (
            "Du hast deinen Crush für dieses Event bereits angegeben. Dein Crush "
            "Coach ruft dich innerhalb von 48 Stunden an, um darüber zu sprechen."
        ),
        "blocked_flash": "Du kannst dich nicht mit diesem Mitglied verbinden.",
        "closed_flash": (
            "Das Verbindungsfenster für dieses Event ist geschlossen. Deine "
            "Verbindungen findest du hier jederzeit."
        ),
        "detail_title": "Mein Crush! – mit deinem Coach",
        "detail_declared": "Crush erklärt",
        "detail_private": (
            "Das bleibt völlig privat – Ben wurde nicht benachrichtigt und wird "
            "es auch nie, es sei denn, dein Coach stellt euch vor."
        ),
        "detail_call": (
            "Dein Crush Coach ruft dich innerhalb von 48 Stunden an, hört deine "
            "Geschichte und plant die Vorstellung."
        ),
        "detail_upsell": (
            "Möchtest du, dass dein Coach jede Woche für dich arbeitet? Frag "
            "während des Gesprächs nach Crush Connect."
        ),
        "detail_told": "Was du deinem Coach erzählt hast:",
        "my_connections": "Mein Crush! – dein Coach ruft dich an",
        "inline_confirm": (
            "Deinen Crush erklären? Das bleibt völlig privat – die Person wird "
            "nie benachrichtigt – und lässt sich nicht rückgängig machen. Dein "
            "Crush Coach ruft dich innerhalb von 48 Stunden an, um darüber zu "
            "sprechen."
        ),
        "inline_placeholder": "Erzähle deinem Coach, was passiert ist (optional)...",
        "success_badge": "Crush erklärt",
        "success_private": (
            "Völlig privat – die Person erfährt nie davon, es sei denn, dein "
            "Coach stellt euch vor."
        ),
        "success_call": (
            "Dein Crush Coach ruft dich innerhalb von 48 Stunden an, um darüber "
            "zu sprechen."
        ),
    },
    "fr": {
        "title": "Mon coup de cœur — comment ça marche",
        "intro": (
            "Qui a attiré votre regard ? Déclarez votre coup de cœur — cela "
            "reste totalement privé et la personne n'est jamais prévenue. Votre "
            "Crush Coach vous appellera sous 48 heures pour écouter votre "
            "histoire et organiser personnellement la présentation."
        ),
        "allowance_title": "Un coup de cœur par événement",
        "allowance": (
            "Vous pouvez déclarer <strong>1</strong> coup de cœur lors de cet "
            "événement. Choisissez bien : votre coach vous appellera à ce sujet."
        ),
        "button": "Mon coup de cœur !",
        "page_title": "Mon coup de cœur ! 💘",
        "form_heading": "Racontez à votre coach ce qu'il s'est passé (optionnel)",
        "private_note": (
            "Seul votre Crush Coach lira ceci — jamais la personne concernée."
        ),
        "next_steps": (
            "Envie de savoir comment cela fonctionne chaque semaine ? Parlez de "
            "Crush Connect à votre coach lors de l'appel"
        ),
        "declared_flash": (
            "Coup de cœur déclaré 💕 Cela reste totalement privé — la personne "
            "ne le saura jamais, sauf si votre coach organise la présentation. "
            "Votre Crush Coach vous appellera sous 48 heures pour en discuter."
        ),
        "used_title": "Coup de cœur déclaré pour cet événement",
        "used_body": (
            "Vous avez utilisé votre coup de cœur pour cet événement. Votre "
            "Crush Coach vous appellera sous 48 heures pour en discuter."
        ),
        "card_state": "Coup de cœur déclaré — votre coach vous appellera",
        "duplicate_flash": (
            "Vous avez déjà déclaré votre coup de cœur pour cette personne."
        ),
        "limit_flash": (
            "Vous avez déjà déclaré votre coup de cœur pour cet événement. "
            "Votre Crush Coach vous appellera sous 48 heures pour en discuter."
        ),
        "blocked_flash": "Vous ne pouvez pas entrer en contact avec ce membre.",
        "closed_flash": (
            "La fenêtre de connexion pour cet événement est fermée. Vos "
            "connexions restent toujours disponibles ici."
        ),
        "detail_title": "Mon coup de cœur — avec votre coach",
        "detail_declared": "Coup de cœur déclaré",
        "detail_private": (
            "Cela reste totalement privé — Ben n'a pas été prévenu(e) et ne le "
            "sera jamais, sauf si votre coach organise la présentation."
        ),
        "detail_call": (
            "Votre Crush Coach vous appellera sous 48 heures pour écouter votre "
            "histoire et organiser la présentation."
        ),
        "detail_upsell": (
            "Envie que votre coach travaille pour vous chaque semaine ? Parlez de "
            "Crush Connect pendant votre appel."
        ),
        "detail_told": "Ce que vous avez dit à votre coach :",
        "my_connections": "Mon coup de cœur — votre coach vous appellera",
        "inline_confirm": (
            "Déclarer votre coup de cœur ? Cela reste totalement privé — la "
            "personne n'est jamais prévenue — et c'est irréversible. Votre Crush "
            "Coach vous appellera sous 48 heures pour en discuter."
        ),
        "inline_placeholder": "Racontez à votre coach ce qu'il s'est passé (optionnel)...",
        "success_badge": "Coup de cœur déclaré",
        "success_private": (
            "Totalement privé — la personne ne le saura jamais, sauf si votre "
            "coach organise la présentation."
        ),
        "success_call": (
            "Votre Crush Coach vous appellera sous 48 heures pour en discuter."
        ),
    },
}

CRUSH_EN = (
    "My Crush! — How It Works",
    "One crush per event",
    "Crush declared for this event",
    "My Crush! declared 💕",
    "You've already declared your crush on this person.",
    "The connection window for this event has closed. Your connections are "
    "always available here.",
    "My Crush! — with your coach",
    "What you told your coach:",
    "My Crush! — your coach will call you",
    "Crush declared — your coach will call you",
)

# Old fuzzy msgstrs that belonged to other messages ("Quiz ...", "Match pro
# Woche", "Spark", "Premium werden") - none may reach a member.
CRUSH_STALE = {
    "de": (
        "Ein Match pro Woche",
        "Quiz für dieses Event erstellt",
        "Kein Problem – dein Coach wählt jemand anderen",
        "Dein Abenteuer beginnen",
        "Meine Crush Sparks",
        "Premium werden",
        "Dieser Spark",
        "Verbindung anfragen",
    ),
    "fr": (
        "Un match par semaine",
        "Quiz créé pour cet événement",
        "votre coach choisira quelqu’un d’autre",
        "Mes Crush Sparks",
        "Passer à Premium",
        "Ce Spark",
        "Demander une connexion",
    ),
}


@pytest.fixture
def ended_event(settings):
    """Event that ended an hour ago: the 48h connection window is open."""
    settings.CRUSH_EVENT_LOBBY_ENABLED = False  # My Crush! applies (no recap CTA)
    return _make_event(starts_in_minutes=-180, duration=120)


def _attendees(event, *names):
    members = [
        _make_member(name, gender="M" if i else "F") for i, name in enumerate(names)
    ]
    for member in members:
        _attend(member, event)
    return members


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_attendees_page_before_declaring(client, ended_event, lang):
    alice, ben = _attendees(ended_event, "alice", "ben")
    _login(client, alice)
    expected = CRUSH[lang]

    response = client.get(f"/{lang}/events/{ended_event.pk}/attendees/")

    assert response.status_code == 200
    body = page(response)
    assert_all(
        body,
        [
            expected["title"],
            expected["intro"],
            expected["allowance_title"],
            expected["allowance"],
        ],
        lang,
    )
    assert re.search(rf">\s*{re.escape(expected['button'])}\s*</span>", body)
    assert_none(body, CRUSH_EN, lang)
    assert_none(body, CRUSH_STALE[lang], lang)
    # Negative control: nothing has been declared yet, so the "used" state must
    # not show.
    assert expected["used_title"] not in body


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_declare_crush_flow_in_translation(client, ended_event, lang):
    alice, ben, chloe = _attendees(ended_event, "alice", "ben", "chloe")
    _login(client, alice)
    expected = CRUSH[lang]
    connect = f"/{lang}/events/{ended_event.pk}/connect/{{pk}}/"

    form = page(client.get(connect.format(pk=ben.pk)))
    assert_all(
        form,
        [
            expected["page_title"],
            expected["form_heading"],
            expected["private_note"],
            expected["next_steps"],
        ],
        lang,
    )
    assert_none(
        form, ("Only your Crush Coach will read this", "Curious how this works"), lang
    )

    declared = client.post(connect.format(pk=ben.pk), {"note": "hi"}, follow=True)

    body = page(declared)
    assert_all(
        body,
        [
            expected["declared_flash"],
            expected["used_title"],
            expected["used_body"],
            expected["card_state"],
        ],
        lang,
    )
    assert_none(body, CRUSH_EN, lang)
    assert_none(body, CRUSH_STALE[lang], lang)
    # Behaviour unchanged: one coach lead was created and the allowance box is
    # gone, not duplicated.
    lead = EventConnection.objects.get(requester=alice, recipient=ben)
    assert lead.flow == EventConnection.FLOW_CRUSH
    assert expected["allowance_title"] not in body

    duplicate = client.post(connect.format(pk=ben.pk), {"note": "hi"}, follow=True)
    assert expected["duplicate_flash"] in page(duplicate)

    limit = client.post(connect.format(pk=chloe.pk), {"note": "hi"}, follow=True)
    assert expected["limit_flash"] in page(limit)
    assert EventConnection.objects.filter(requester=alice).count() == 1

    detail = page(client.get(f"/{lang}/connections/{lead.pk}/"))
    assert_all(
        detail,
        [
            expected["detail_title"],
            expected["detail_declared"],
            expected["detail_private"],
            expected["detail_call"],
            expected["detail_upsell"],
            expected["detail_told"],
        ],
        lang,
    )
    assert_none(detail, CRUSH_EN, lang)
    assert_none(detail, CRUSH_STALE[lang], lang)

    listing = page(client.get(f"/{lang}/connections/"))
    assert expected["my_connections"] in listing
    assert "My Crush! — your coach will call you" not in listing


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_inline_declare_form_and_success_in_translation(client, ended_event, lang):
    alice, ben = _attendees(ended_event, "alice", "ben")
    _login(client, alice)
    expected = CRUSH[lang]
    inline = f"/{lang}/events/{ended_event.pk}/connect-inline/{ben.pk}/"

    form = page(client.get(inline, HTTP_HX_REQUEST="true"))
    assert f'hx-confirm="{expected["inline_confirm"]}"' in form
    assert f'placeholder="{expected["inline_placeholder"]}"' in form
    assert expected["private_note"] in form

    success = page(client.post(inline, {"note": "hi"}, HTTP_HX_REQUEST="true"))
    assert_all(
        success,
        [
            expected["success_badge"],
            expected["success_private"],
            expected["success_call"],
        ],
        lang,
    )
    assert "My Crush! declared" not in success
    assert EventConnection.objects.filter(requester=alice, recipient=ben).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("lang", LANGS)
def test_blocked_pair_and_closed_window_messages(client, ended_event, lang):
    alice, ben = _attendees(ended_event, "alice", "ben")
    _login(client, alice)
    expected = CRUSH[lang]
    UserBlock.objects.create(blocker=ben, blocked=alice)

    blocked = client.post(
        f"/{lang}/events/{ended_event.pk}/connect/{ben.pk}/", {"note": "x"}, follow=True
    )

    assert expected["blocked_flash"] in page(blocked)
    assert not EventConnection.objects.exists()

    UserBlock.objects.all().delete()
    _end_event(ended_event, hours_ago=60)  # connection window (48h) is over
    closed = client.get(f"/{lang}/events/{ended_event.pk}/attendees/", follow=True)
    assert expected["closed_flash"] in page(closed)


# ---------------------------------------------------------------------------
# 5. Catalogue hygiene for every entry this change touches
# ---------------------------------------------------------------------------

# msgids (source language) whose DE/FR entries this change filled or replaced.
P44_MSGIDS = (
    "I agree my clear photo is shown to the few people matched to me each day so "
    "they can guess my questions.",
    "I understand my photo, first name, age range and the 3 questions I chose "
    "appear on my card to the members matched with me, and I can be removed from "
    "Crush Connect at any time.",
    "Pick exactly %(n)d questions and answer each about yourself.",
    "Yes — that's me",
    "Save my guesses",
    "You've read %(name)s today.",
    "Connect Week card for %(name)s",
    "You met at a Crush.lu event.",
    "Hide or remove this person",
    "They will disappear from People I've Met immediately. Your reason and "
    "details are private and reviewed only by Support.",
    "Select a reason",
    "Private details (optional)",
    "Tell Support anything they should know.",
    "Hide person and request review",
    "A permanent record of a real-world meeting. Say hi next time you see them.",
    "No one here yet",
    "When you and someone you met at an event both confirm it, they'll appear " "here.",
    "The Event Lobby is where checked-in Crush Connect members can quietly "
    "signal who they'd like to meet tonight. Complete your Crush Connect "
    "profile before the event ends and you'll join instantly.",
    "The live lobby has ended",
    "This event's lobby is closed.",
    "This person is now hidden. Support will review your private request.",
    "Please select a valid reason.",
    "%(name)s was added to People I've Met.",
    "I feel unsafe or uncomfortable",
    "I no longer want this person visible",
    "Another reason",
    "Privacy concern",
    "We did not actually meet",
    "Crush declared — your coach will call you",
    "Find them in your event recap",
    "Crush used for this event",
    "My Crush!",
    "My Crush! — How It Works",
    "Who caught your eye? Declare your crush — it's completely private and "
    "they'll never be notified. Your Crush Coach will call you within 48 hours "
    "to hear the story and personally arrange the introduction.",
    "Browse the room and tap “My Crush!” on anyone who caught your eye. It "
    "stays private — your Crush Coach calls you within 48 hours to talk it "
    "through.",
    "One crush per event",
    "You can declare <strong>%(remaining)s</strong> crush at this event. Choose "
    "wisely — your coach will call you about it.",
    "Crush declared for this event",
    "You've used your crush for this event. Your Crush Coach will call you "
    "within 48 hours to talk about it.",
    "My Crush! 💘",
    "Only your Crush Coach will read this — never your crush.",
    "Curious how this works every week? Ask your coach about Crush Connect on "
    "the call",
    "Declare your crush? It stays completely private — they are never notified "
    "— and it cannot be undone. Your Crush Coach will call you within 48 hours "
    "to talk about it.",
    "Tell your coach what happened (optional)...",
    "My Crush! — with your coach",
    "My Crush! declared",
    "What you told your coach:",
    "It's completely private — %(name)s has not been notified and never will be "
    "unless your coach makes the introduction.",
    "Completely private — they'll never know unless your coach makes the "
    "introduction.",
    "Your Crush Coach will call you within 48 hours to talk about it.",
    "Your Crush Coach will call you within 48 hours to hear the story and plan "
    "the introduction.",
    "Want your coach working for you every week? Ask about Crush Connect during "
    "your call.",
    "The connection window for this event has closed. Your connections are "
    "always available here.",
    "You cannot connect with this member.",
    "You've already declared your crush on this person.",
    "You've already declared your crush for this event. Your Crush Coach will "
    "call you within 48 hours to talk about it.",
    "My Crush! declared 💕 It's completely private — they'll never know unless "
    "your coach makes the introduction. Your Crush Coach will call you within "
    "48 hours to talk about it.",
    "This connection is no longer available.",
    "My Crush! — your coach will call you",
)

PLACEHOLDER = re.compile(r"%\(\w+\)[sd]|%[sd]|</?[a-zA-Z][^>]*>")
DE_FORMAL = re.compile(r"\b(Sie|Ihnen|Ihre[mnrs]?)\b")  # "Ihr" is informal plural
FR_INFORMAL = re.compile(r"\b(tu|ton|tes|toi|ta)\b", re.IGNORECASE)


def _entries(lang):
    catalogue = polib.pofile(str(LOCALE_DIR / lang / "LC_MESSAGES" / "django.po"))
    entries = {}
    for entry in catalogue:
        if not entry.obsolete and entry.msgctxt is None:
            entries.setdefault(entry.msgid, []).append(entry)
    return entries


def _forms(entry):
    if entry.msgid_plural:
        return [entry.msgstr_plural[0], entry.msgstr_plural[1]]
    return [entry.msgstr]


@pytest.mark.parametrize("lang", LANGS)
def test_every_touched_entry_is_translated_not_fuzzy_and_placeholder_safe(lang):
    entries = _entries(lang)
    problems = []
    for msgid in P44_MSGIDS:
        found = entries.get(msgid, [])
        if len(found) != 1:
            problems.append(f"{msgid[:60]!r}: {len(found)} entries")
            continue
        entry = found[0]
        if "fuzzy" in entry.flags:
            problems.append(f"{msgid[:60]!r}: still fuzzy")
        forms = _forms(entry)
        if not all(form.strip() for form in forms):
            problems.append(f"{msgid[:60]!r}: empty msgstr")
            continue
        if any(form == msgid for form in forms):
            problems.append(f"{msgid[:60]!r}: msgstr equals the English msgid")
        sources = [msgid] + ([entry.msgid_plural] if entry.msgid_plural else [])
        for form in forms:
            if Counter(PLACEHOLDER.findall(form)) != Counter(
                PLACEHOLDER.findall(sources[0])
            ):
                problems.append(f"{msgid[:60]!r}: placeholders/tags differ in {form!r}")
        if lang == "de" and any(DE_FORMAL.search(form) for form in forms):
            problems.append(f"{msgid[:60]!r}: formal German address")
        if lang == "fr" and any(FR_INFORMAL.search(form) for form in forms):
            problems.append(f"{msgid[:60]!r}: informal French address")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("lang", LANGS)
def test_compiled_catalogue_serves_every_touched_entry(lang):
    """The .mo must carry what the .po says (built only via polib by
    scripts/build_assets.py; a stale or malformed .mo would fall back to
    English silently)."""
    entries = _entries(lang)
    problems = []
    with translation.override(lang):
        for msgid in P44_MSGIDS:
            entry = entries[msgid][0]
            if entry.msgid_plural:
                singular = translation.ngettext(msgid, entry.msgid_plural, 1)
                plural = translation.ngettext(msgid, entry.msgid_plural, 2)
                if [singular, plural] != _forms(entry):
                    problems.append(f"{msgid[:60]!r}: plural forms not served")
            elif translation.gettext(msgid) != entry.msgstr:
                problems.append(f"{msgid[:60]!r}: .mo differs from .po")
    assert not problems, "\n".join(problems)
