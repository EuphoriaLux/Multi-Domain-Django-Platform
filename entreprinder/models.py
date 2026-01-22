# entreprinder/models.py
"""
Models for the Entreprinder business networking platform.

This module contains:
- Industry, Skill: Categorization models
- EntrepreneurProfile: Extended user profile with business details
- Match, Like, Dislike: Matching system models (merged from matching app)
- Vibe Coding models (imported from vibe submodule)
"""

from django.db import models
from django.contrib.auth.models import User

# Import submodule models so Django migrations can find them
from entreprinder.vibe.models import (
    PixelCanvas, Pixel, PixelHistory, UserPixelCooldown, UserPixelStats
)
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


class Industry(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Skill(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class EntrepreneurProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    linkedin_photo_url = models.URLField(blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True)
    tagline = models.CharField(max_length=150, blank=True, help_text="A brief, catchy description of yourself")
    company = models.CharField(max_length=100, blank=True)
    industry = models.ForeignKey(Industry, on_delete=models.SET_NULL, null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)
    skills = models.ManyToManyField(Skill, related_name='entrepreneurs')
    looking_for = models.TextField(max_length=500, blank=True)
    offering = models.TextField(max_length=500, blank=True, help_text="What can you offer to other entrepreneurs?")
    website = models.URLField(blank=True)
    linkedin_profile = models.URLField(blank=True)
    years_of_experience = models.PositiveIntegerField(default=0)
    is_mentor = models.BooleanField(default=False)
    is_looking_for_funding = models.BooleanField(default=False)
    is_investor = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username}'s profile"

    def get_profile_picture_url(self):
        """Return LinkedIn photo URL or default placeholder."""
        if self.linkedin_photo_url:
            return self.linkedin_photo_url
        return '/static/entreprinder/images/default-avatar.png'


# =============================================================================
# Matching Models (merged from matching app)
# =============================================================================

class Match(models.Model):
    """Mutual match between two entrepreneurs."""
    entrepreneur1 = models.ForeignKey(
        EntrepreneurProfile,
        on_delete=models.CASCADE,
        related_name='matches_as_first'
    )
    entrepreneur2 = models.ForeignKey(
        EntrepreneurProfile,
        on_delete=models.CASCADE,
        related_name='matches_as_second'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'matching_match'  # Keep existing database table
        unique_together = ('entrepreneur1', 'entrepreneur2')

    def __str__(self):
        return f"Match: {self.entrepreneur1} <-> {self.entrepreneur2}"


class Like(models.Model):
    """User liked another entrepreneur's profile."""
    liker = models.ForeignKey(
        EntrepreneurProfile,
        on_delete=models.CASCADE,
        related_name='likes_given'
    )
    liked = models.ForeignKey(
        EntrepreneurProfile,
        on_delete=models.CASCADE,
        related_name='likes_received'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'matching_like'  # Keep existing database table
        unique_together = ('liker', 'liked')
        indexes = [
            models.Index(fields=['liker', 'liked']),
        ]

    def __str__(self):
        return f"{self.liker} likes {self.liked}"

    def save(self, *args, **kwargs):
        # Check if a like already exists
        if not self.__class__.objects.filter(liker=self.liker, liked=self.liked).exists():
            super().save(*args, **kwargs)


class Dislike(models.Model):
    """User passed on another entrepreneur's profile."""
    disliker = models.ForeignKey(
        EntrepreneurProfile,
        on_delete=models.CASCADE,
        related_name='dislikes_given'
    )
    disliked = models.ForeignKey(
        EntrepreneurProfile,
        on_delete=models.CASCADE,
        related_name='dislikes_received'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'matching_dislike'  # Keep existing database table
        unique_together = ('disliker', 'disliked')

    def __str__(self):
        return f"{self.disliker} passed on {self.disliked}"

    def save(self, *args, **kwargs):
        # Check if a dislike already exists
        if not self.__class__.objects.filter(disliker=self.disliker, disliked=self.disliked).exists():
            super().save(*args, **kwargs)


# =============================================================================
# Signals
# =============================================================================

def _create_match_if_mutual_like(like):
    """Creates a match if there's a mutual like."""
    mutual_like = Like.objects.filter(liker=like.liked, liked=like.liker).exists()
    if mutual_like:
        try:
            Match.objects.get_or_create(
                entrepreneur1=like.liker,
                entrepreneur2=like.liked
            )
        except Exception as e:
            print(f"Error creating match: {e}")


@receiver(post_save, sender=Like)
def create_match(sender, instance, created, **kwargs):
    """Signal to create a match when mutual likes exist."""
    if created:
        _create_match_if_mutual_like(instance)
