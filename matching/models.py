from django.db import models
from entreprinder.models import EntrepreneurProfile

class Match(models.Model):
    entrepreneur1 = models.ForeignKey(EntrepreneurProfile, on_delete=models.CASCADE, related_name='matches_as_first')
    entrepreneur2 = models.ForeignKey(EntrepreneurProfile, on_delete=models.CASCADE, related_name='matches_as_second')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('entrepreneur1', 'entrepreneur2')

class Like(models.Model):
    liker = models.ForeignKey(EntrepreneurProfile, on_delete=models.CASCADE, related_name='likes_given')
    liked = models.ForeignKey(EntrepreneurProfile, on_delete=models.CASCADE, related_name='likes_received')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('liker', 'liked')
        indexes = [
            models.Index(fields=['liker', 'liked']),
        ]

class Dislike(models.Model):
    disliker = models.ForeignKey(EntrepreneurProfile, on_delete=models.CASCADE, related_name='dislikes_given')
    disliked = models.ForeignKey(EntrepreneurProfile, on_delete=models.CASCADE, related_name='dislikes_received')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('disliker', 'disliked')

from django.db.models.signals import post_save
from django.dispatch import receiver

def _create_match_if_mutual_like(like):
    """
    Creates a match if there's a mutual like.
    """
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
    if created:
        _create_match_if_mutual_like(instance)
