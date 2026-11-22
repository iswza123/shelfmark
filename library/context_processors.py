from django.conf import settings


def library_rules(request):
    """Make the lending rules available to every template."""
    return {
        'LOAN_PERIOD_DAYS': settings.LOAN_PERIOD_DAYS,
        'FINE_PER_DAY': settings.FINE_PER_DAY,
        'MAX_ACTIVE_LOANS': settings.MAX_ACTIVE_LOANS,
    }
