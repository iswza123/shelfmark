from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        verbose_name_plural = 'categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class BookQuerySet(models.QuerySet):
    def with_availability(self):
        """Annotate every book with `active_loans` and `available` copies."""
        return self.annotate(
            active_loans=Count('loans', filter=Q(loans__returned_on__isnull=True)),
        ).annotate(
            available=F('total_copies') - F('active_loans'),
        )


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=150)
    isbn = models.CharField('ISBN', max_length=13, unique=True)
    category = models.ForeignKey(
        Category, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='books',
    )
    description = models.TextField(blank=True)
    published_year = models.PositiveSmallIntegerField(null=True, blank=True)
    total_copies = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    added_on = models.DateTimeField(auto_now_add=True)

    objects = BookQuerySet.as_manager()

    class Meta:
        ordering = ['title']

    def __str__(self):
        return f'{self.title} by {self.author}'

    def get_absolute_url(self):
        return reverse('book_detail', args=[self.pk])

    @property
    def available_copies(self):
        """Copies on the shelf right now (works on non-annotated books too)."""
        return self.total_copies - self.loans.filter(returned_on__isnull=True).count()

    # Used only for the book-spine artwork in the templates.
    @property
    def spine_hue(self):
        return (self.pk or 0) * 47 % 360

    @property
    def spine_height(self):
        return 150 + (self.pk or 0) * 13 % 56


class LoanQuerySet(models.QuerySet):
    def active(self):
        return self.filter(returned_on__isnull=True)

    def overdue(self):
        return self.active().filter(due_date__lt=timezone.localdate())

    def returned(self):
        return self.filter(returned_on__isnull=False)

    def unpaid_fines(self):
        return self.returned().filter(fine__gt=0, fine_paid=False)


def default_due_date():
    return timezone.localdate() + timedelta(days=settings.LOAN_PERIOD_DAYS)


class Loan(models.Model):
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name='loans')
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='loans',
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='loans_issued',
    )
    issued_on = models.DateField(default=timezone.localdate)
    due_date = models.DateField(default=default_due_date)
    returned_on = models.DateField(null=True, blank=True)
    fine = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    fine_paid = models.BooleanField(default=False)

    objects = LoanQuerySet.as_manager()

    class Meta:
        ordering = ['-issued_on', '-id']

    def __str__(self):
        return f'{self.book.title} -> {self.member.username}'

    # ----- state -----------------------------------------------------------
    @property
    def is_returned(self):
        return self.returned_on is not None

    @property
    def days_overdue(self):
        """Days late. Counts up to today while the book is still out."""
        end = self.returned_on or timezone.localdate()
        return max(0, (end - self.due_date).days)

    @property
    def is_overdue(self):
        return not self.is_returned and self.days_overdue > 0

    @property
    def current_fine(self):
        """Fine owed right now (final once the book is returned)."""
        if self.is_returned:
            return self.fine
        return self.days_overdue * settings.FINE_PER_DAY

    @property
    def status(self):
        if self.is_returned:
            return 'returned'
        return 'overdue' if self.is_overdue else 'active'

    # ----- actions ---------------------------------------------------------
    def return_book(self, on=None):
        """Mark as returned, freezing the fine. Fines of 0 count as settled."""
        self.returned_on = on or timezone.localdate()
        self.fine = self.days_overdue * settings.FINE_PER_DAY
        self.fine_paid = self.fine == 0
        self.save(update_fields=['returned_on', 'fine', 'fine_paid'])
