from django.contrib import admin
from .models import Match, Like, Dislike

@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('entrepreneur1', 'entrepreneur2', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('entrepreneur1__user__username', 'entrepreneur2__user__username')
    date_hierarchy = 'created_at'

@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ('liker', 'liked', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('liker__user__username', 'liked__user__username')
    date_hierarchy = 'created_at'

@admin.register(Dislike)
class DislikeAdmin(admin.ModelAdmin):
    list_display = ('disliker', 'disliked', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('disliker__user__username', 'disliked__user__username')
    date_hierarchy = 'created_at'

class MatchInline(admin.TabularInline):
    model = Match
    fk_name = 'entrepreneur1'
    extra = 1