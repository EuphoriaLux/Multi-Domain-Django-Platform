"""django-modeltranslation registration for hub models.

``PartnerOffer`` titles and descriptions carry EN/DE/FR variants, the same way
``crush_lu.MeetupEvent`` does, so an offer can prefill an event in all three.
"""

from modeltranslation.translator import TranslationOptions, translator

from .models import PartnerOffer


class PartnerOfferTranslationOptions(TranslationOptions):
    fields = ("title", "description")


translator.register(PartnerOffer, PartnerOfferTranslationOptions)
