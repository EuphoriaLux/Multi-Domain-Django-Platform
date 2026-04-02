from django.db.models import Q, Count, Avg
from django.utils.translation import gettext as _


def get_preference_stats(approved_qs):
    """
    Compute Ideal Crush preference statistics for a queryset of approved profiles.
    Returns a dict suitable for both admin and coach dashboard templates.
    """
    from .models import CrushProfile

    total_approved = approved_qs.count()

    # "Configured" = any preference field differs from its default
    configured_filter = (
        Q(preferred_age_min__gt=18)
        | Q(preferred_age_max__lt=99)
        | ~Q(preferred_genders=[])
        | ~Q(first_step_preference='')
    )
    configured_qs = approved_qs.filter(configured_filter)
    configured_count = configured_qs.count()
    adoption_rate = (
        round(configured_count / total_approved * 100, 1)
        if total_approved > 0
        else 0
    )

    # --- Age range averages (among those who customized age) ---
    custom_age_qs = approved_qs.filter(
        Q(preferred_age_min__gt=18) | Q(preferred_age_max__lt=99)
    )
    age_agg = custom_age_qs.aggregate(
        avg_min=Avg('preferred_age_min'),
        avg_max=Avg('preferred_age_max'),
    )
    avg_age_min = round(age_agg['avg_min']) if age_agg['avg_min'] else None
    avg_age_max = round(age_agg['avg_max']) if age_agg['avg_max'] else None

    # --- Gender preference distribution (iterate JSONField in Python) ---
    gender_label_map = dict(CrushProfile.GENDER_CHOICES)
    gender_counts = {}
    for prefs in configured_qs.exclude(preferred_genders=[]).values_list(
        'preferred_genders', flat=True
    ):
        if isinstance(prefs, list):
            for code in prefs:
                gender_counts[code] = gender_counts.get(code, 0) + 1

    total_gender_selections = sum(gender_counts.values()) or 1
    gender_pref_stats = sorted(
        [
            {
                'code': code,
                'label': str(gender_label_map.get(code, code)),
                'count': count,
                'pct': round(count / total_gender_selections * 100, 1),
            }
            for code, count in gender_counts.items()
        ],
        key=lambda x: x['count'],
        reverse=True,
    )
    most_popular_gender_label = (
        gender_pref_stats[0]['label'] if gender_pref_stats else '\u2014'
    )

    # --- Age bracket distribution (based on preferred_age_min) ---
    age_brackets = [
        ('18\u201324', 18, 24),
        ('25\u201334', 25, 34),
        ('35\u201344', 35, 44),
        ('45\u201354', 45, 54),
        ('55+', 55, 999),
    ]
    total_custom_age = custom_age_qs.count() or 1
    age_distribution = []
    for label, lo, hi in age_brackets:
        count = custom_age_qs.filter(
            preferred_age_min__gte=lo, preferred_age_min__lte=hi
        ).count()
        age_distribution.append(
            {
                'label': label,
                'count': count,
                'pct': round(count / total_custom_age * 100, 1),
            }
        )

    # --- First step preference distribution ---
    first_step_labels = dict(CrushProfile.FIRST_STEP_CHOICES)
    first_step_data = (
        approved_qs.exclude(first_step_preference='')
        .values('first_step_preference')
        .annotate(count=Count('id'))
    )
    total_first_step = sum(item['count'] for item in first_step_data) or 1
    first_step_stats = [
        {
            'key': item['first_step_preference'],
            'label': str(
                first_step_labels.get(
                    item['first_step_preference'],
                    item['first_step_preference'],
                )
            ),
            'count': item['count'],
            'pct': round(item['count'] / total_first_step * 100, 1),
        }
        for item in first_step_data
    ]

    # --- Unconfigured profiles for coach follow-up (max 50) ---
    unconfigured_qs = (
        approved_qs.filter(
            preferred_age_min=18,
            preferred_age_max=99,
            preferred_genders=[],
            first_step_preference='',
        )
        .select_related('user')
        .order_by('-created_at')[:50]
    )
    unconfigured_profiles = [
        {'display_name': p.display_name, 'id': p.id} for p in unconfigured_qs
    ]

    return {
        'configured_count': configured_count,
        'total_approved': total_approved,
        'adoption_rate': adoption_rate,
        'avg_age_min': avg_age_min,
        'avg_age_max': avg_age_max,
        'most_popular_gender_label': most_popular_gender_label,
        'gender_pref_stats': gender_pref_stats,
        'age_distribution': age_distribution,
        'first_step_stats': first_step_stats,
        'unconfigured_profiles': unconfigured_profiles,
    }
