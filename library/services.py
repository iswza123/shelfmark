"""Business rules for lending. Views call these so the rules live in one place."""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Book, Loan, default_due_date


class LoanError(Exception):
    """Raised when a book cannot be issued. The message is safe to show."""


def member_standing(member):
    """Summary of a member's account used for the borrowing rules."""
    loans = Loan.objects.filter(member=member)
    active = loans.active()
    overdue = [loan for loan in active if loan.is_overdue]

    cents = Decimal('0.01')
    unpaid_returned = (loans.unpaid_fines().aggregate(t=Sum('fine'))['t'] or Decimal('0')).quantize(cents)
    accruing = sum((loan.current_fine for loan in overdue), Decimal('0')).quantize(cents)

    return {
        'active_count': active.count(),
        'overdue_count': len(overdue),
        'unpaid_fines': unpaid_returned,
        'accruing_fines': accruing,
        'total_owed': unpaid_returned + accruing,
    }


@transaction.atomic
def issue_book(book, member, issued_by=None):
    """Lend `book` to `member`, enforcing every library rule."""
    # Lock the row so two librarians can't hand out the last copy together.
    book = Book.objects.select_for_update().get(pk=book.pk)

    if member.is_staff:
        raise LoanError('Librarians cannot borrow books. Choose a member account.')

    if book.available_copies <= 0:
        raise LoanError(f'No copies of "{book.title}" are available right now.')

    if Loan.objects.active().filter(book=book, member=member).exists():
        raise LoanError(f'{member.username} already has "{book.title}" on loan.')

    standing = member_standing(member)

    if standing['active_count'] >= settings.MAX_ACTIVE_LOANS:
        raise LoanError(
            f'{member.username} already has {standing["active_count"]} books out '
            f'(the limit is {settings.MAX_ACTIVE_LOANS}).'
        )

    if standing['overdue_count']:
        raise LoanError(f'{member.username} has overdue books. Return them first.')

    if standing['unpaid_fines'] > 0:
        raise LoanError(
            f'{member.username} has unpaid fines of Rs. {standing["unpaid_fines"]}. '
            'Settle them before borrowing.'
        )

    return Loan.objects.create(
        book=book,
        member=member,
        issued_by=issued_by,
        issued_on=timezone.localdate(),
        due_date=default_due_date(),
    )
