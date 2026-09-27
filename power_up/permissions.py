"""Authorization helpers shared by Power-Up's internal applications."""

POWER_UP_STAFF_GROUP = "power_up_staff"


def is_power_up_staff(user):
    """Return whether an active user belongs to Power-Up staff.

    Django superusers retain their existing administrative access. Ordinary
    ``is_staff`` status alone is intentionally insufficient: the shared user
    table also grants ``is_staff`` to some non-Power-Up roles (e.g. CrushCoach
    accounts), so membership in the dedicated ``power_up_staff`` group is
    what actually authorizes access to Power-Up's internal surfaces.
    """
    if not getattr(user, "is_authenticated", False):
        return False

    if not getattr(user, "is_active", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    return user.groups.filter(name=POWER_UP_STAFF_GROUP).exists()
