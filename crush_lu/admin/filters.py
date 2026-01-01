"""
Custom admin filters for Crush.lu Coach Panel.

Provides filtering options for coach workflow management.
"""

from django.contrib import admin
from django.utils import timezone
from datetime import timedelta


class ReviewTimeFilter(admin.SimpleListFilter):
    """Filter submissions by how long they've been pending"""
    title = 'Pending Time'
    parameter_name = 'pending_time'

    def lookups(self, request, model_admin):
        return (
            ('24h', '🚨 Pending > 24 hours'),
            ('3d', '⚠️ Pending > 3 days'),
            ('7d', '🔴 Pending > 7 days'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == '24h':
            cutoff = now - timedelta(hours=24)
            return queryset.filter(status='pending', submitted_at__lt=cutoff)
        elif self.value() == '3d':
            cutoff = now - timedelta(days=3)
            return queryset.filter(status='pending', submitted_at__lt=cutoff)
        elif self.value() == '7d':
            cutoff = now - timedelta(days=7)
            return queryset.filter(status='pending', submitted_at__lt=cutoff)
        return queryset


class CoachAssignmentFilter(admin.SimpleListFilter):
    """Filter profiles by coach assignment status"""
    title = 'Coach Assignment'
    parameter_name = 'coach_assignment'

    def lookups(self, request, model_admin):
        return (
            ('has_coach', '👤 Has Coach Assigned'),
            ('no_coach', '❌ No Coach Assigned'),
            ('not_submitted', '📝 Not Submitted for Review'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'has_coach':
            return queryset.filter(
                profilesubmission__coach__isnull=False
            ).distinct()
        elif self.value() == 'no_coach':
            return queryset.filter(
                profilesubmission__coach__isnull=True
            ).distinct()
        elif self.value() == 'not_submitted':
            return queryset.filter(
                profilesubmission__isnull=True
            )
        return queryset


class SubmissionWorkflowFilter(admin.SimpleListFilter):
    """Filter submissions by workflow stage with visual indicators"""
    title = 'Workflow Stage'
    parameter_name = 'workflow'

    def lookups(self, request, model_admin):
        return (
            ('urgent', '🚨 Needs Attention (Pending >24h)'),
            ('new', '🆕 New (Pending <24h)'),
            ('awaiting_call', '📞 Awaiting Screening Call'),
            ('ready_approve', '✅ Ready to Approve (Call Done)'),
            ('completed', '✔️ Completed'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        cutoff_24h = now - timedelta(hours=24)

        if self.value() == 'urgent':
            # Pending and submitted > 24h ago
            return queryset.filter(
                status='pending',
                submitted_at__lt=cutoff_24h
            )
        elif self.value() == 'new':
            # Pending and submitted < 24h ago
            return queryset.filter(
                status='pending',
                submitted_at__gte=cutoff_24h
            )
        elif self.value() == 'awaiting_call':
            # Has coach, pending status, call not done
            return queryset.filter(
                status='pending',
                coach__isnull=False,
                review_call_completed=False
            )
        elif self.value() == 'ready_approve':
            # Has coach, pending status, call completed
            return queryset.filter(
                status='pending',
                coach__isnull=False,
                review_call_completed=True
            )
        elif self.value() == 'completed':
            # Approved or rejected
            return queryset.filter(
                status__in=['approved', 'rejected']
            )
        return queryset
