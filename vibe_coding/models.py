from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
import json


class PixelCanvas(models.Model):
    name = models.CharField(max_length=100, default="Lux Pixel War")
    width = models.IntegerField(default=100, validators=[MinValueValidator(10), MaxValueValidator(500)])
    height = models.IntegerField(default=100, validators=[MinValueValidator(10), MaxValueValidator(500)])
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    # Cooldown settings for different user types
    anonymous_cooldown_seconds = models.IntegerField(default=30, help_text="Cooldown for anonymous users (seconds)")
    registered_cooldown_seconds = models.IntegerField(default=12, help_text="Cooldown for registered users (seconds)")
    registered_pixels_per_minute = models.IntegerField(default=5, help_text="Max pixels per minute for registered users")
    anonymous_pixels_per_minute = models.IntegerField(default=2, help_text="Max pixels per minute for anonymous users")
    
    def __str__(self):
        return f"{self.name} ({self.width}x{self.height})"
    
    class Meta:
        verbose_name_plural = "Pixel Canvases"


class Pixel(models.Model):
    canvas = models.ForeignKey(PixelCanvas, on_delete=models.CASCADE, related_name='pixels')
    x = models.IntegerField(validators=[MinValueValidator(0)])
    y = models.IntegerField(validators=[MinValueValidator(0)])
    color = models.CharField(max_length=7, default="#FFFFFF")
    placed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    placed_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('canvas', 'x', 'y')
        ordering = ['y', 'x']
    
    def __str__(self):
        return f"Pixel ({self.x}, {self.y}) - {self.color}"


class PixelHistory(models.Model):
    canvas = models.ForeignKey(PixelCanvas, on_delete=models.CASCADE, related_name='history')
    x = models.IntegerField()
    y = models.IntegerField()
    color = models.CharField(max_length=7)
    placed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-placed_at']
    
    def __str__(self):
        return f"History ({self.x}, {self.y}) - {self.color} at {self.placed_at}"


class UserPixelCooldown(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    canvas = models.ForeignKey(PixelCanvas, on_delete=models.CASCADE)
    last_placed = models.DateTimeField(auto_now=True)
    session_key = models.CharField(max_length=100, null=True, blank=True)
    pixels_placed_last_minute = models.IntegerField(default=0)
    last_minute_reset = models.DateTimeField(auto_now_add=True, null=True)
    
    class Meta:
        unique_together = ('user', 'canvas', 'session_key')
        
    def get_cooldown_seconds(self):
        if self.user:
            return self.canvas.registered_cooldown_seconds
        return self.canvas.anonymous_cooldown_seconds
    
    def get_max_pixels_per_minute(self):
        if self.user:
            return self.canvas.registered_pixels_per_minute
        return self.canvas.anonymous_pixels_per_minute


class UserPixelStats(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    canvas = models.ForeignKey(PixelCanvas, on_delete=models.CASCADE)
    total_pixels_placed = models.IntegerField(default=0)
    last_pixel_placed = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('user', 'canvas')
    
    def __str__(self):
        return f"{self.user.username} - {self.total_pixels_placed} pixels"
