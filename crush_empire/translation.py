"""
django-modeltranslation registration for Crush Empire.

Only the *text* is translatable. `is_red_flag` and `flag_type` are language-
neutral booleans/enums on the row — which is the whole reason a bio is a list of
segments rather than a string with highlighted character ranges. "oil rig
engineer" and "Ingenieur auf einer Bohrinsel" share no offsets, but they are the
same segment.

Scam tells must be *authored* per language, not machine-translated. Broken
grammar as a warning sign does not survive a translation pass, and a wrong tell
teaches the wrong lesson.
"""
from modeltranslation.translator import TranslationOptions, translator

from .models.content import BioSegment


class BioSegmentTranslationOptions(TranslationOptions):
    fields = ("text", "explanation")


translator.register(BioSegment, BioSegmentTranslationOptions)
