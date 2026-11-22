from datetime import timedelta
from decimal import Decimal
from io import StringIO
from itertools import count

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import BookForm, IssueBookForm
from .models import Book, Category, Loan
from .services import LoanError, issue_book, member_standing

User = get_user_model()
PASSWORD = 'StrongPass!234'
_isbn = count(1000000000000)


def make_member(username='asha', **extra):
    return User.objects.create_user(username, f'{username}@example.com', PASSWORD, **extra)


def make_librarian(username='lena'):
    return User.objects.create_user(username, f'{username}@example.com', PASSWORD, is_staff=True)


def make_book(title='Clean Code', copies=1, **extra):
    extra.setdefault('author', 'Robert Martin')
    extra.setdefault('isbn', str(next(_isbn)))
    return Book.objects.create(title=title, total_copies=copies, **extra)


def make_loan(book, member, days_ago=0, kept=None):
    """A loan issued `days_ago` days back (due 14 days after issue)."""
    issued = timezone.localdate() - timedelta(days=days_ago)
    loan = Loan.objects.create(
        book=book, member=member, issued_on=issued, due_date=issued + timedelta(days=14),
    )
    if kept is not None:
        loan.return_book(on=issued + timedelta(days=kept))
    return loan


class LibraryTestCase(TestCase):
    def setUp(self):
        self.librarian = make_librarian()
        self.member = make_member()
        self.book = make_book()

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)


# ===========================================================================
# Models
# ===========================================================================

class LoanModelTests(LibraryTestCase):

    def test_on_time_loan_has_no_fine(self):
        loan = make_loan(self.book, self.member, days_ago=5)
        self.assertEqual(loan.status, 'active')
        self.assertEqual(loan.days_overdue, 0)
        self.assertEqual(loan.current_fine, 0)

    def test_due_today_is_not_overdue(self):
        loan = make_loan(self.book, self.member, days_ago=14)
        self.assertFalse(loan.is_overdue)

    def test_overdue_fine_grows_per_day(self):
        loan = make_loan(self.book, self.member, days_ago=17)   # 3 days late
        self.assertTrue(loan.is_overdue)
        self.assertEqual(loan.status, 'overdue')
        self.assertEqual(loan.days_overdue, 3)
        self.assertEqual(loan.current_fine, Decimal('6.00'))

    def test_return_on_time_settles_immediately(self):
        loan = make_loan(self.book, self.member, days_ago=10, kept=9)
        self.assertEqual(loan.status, 'returned')
        self.assertEqual(loan.fine, 0)
        self.assertTrue(loan.fine_paid)

    def test_late_return_freezes_fine_and_leaves_it_unpaid(self):
        loan = make_loan(self.book, self.member, days_ago=40, kept=20)   # 6 days late
        loan.refresh_from_db()
        self.assertEqual(loan.fine, Decimal('12.00'))
        self.assertFalse(loan.fine_paid)
        self.assertEqual(loan.current_fine, Decimal('12.00'))   # does not keep growing
        self.assertFalse(loan.is_overdue)

    @override_settings(FINE_PER_DAY=Decimal('5.00'))
    def test_fine_rate_comes_from_settings(self):
        loan = make_loan(self.book, self.member, days_ago=16)
        self.assertEqual(loan.current_fine, Decimal('10.00'))

    def test_queryset_helpers(self):
        make_loan(self.book, self.member, days_ago=2)
        late = make_loan(make_book('B'), self.member, days_ago=20)
        done = make_loan(make_book('C'), self.member, days_ago=30, kept=25)
        self.assertEqual(Loan.objects.active().count(), 2)
        self.assertEqual(list(Loan.objects.overdue()), [late])
        self.assertEqual(list(Loan.objects.returned()), [done])
        self.assertEqual(list(Loan.objects.unpaid_fines()), [done])


class BookModelTests(LibraryTestCase):

    def test_availability_annotation_and_property_agree(self):
        book = make_book('Two copies', copies=2)
        make_loan(book, self.member, days_ago=1)
        annotated = Book.objects.with_availability().get(pk=book.pk)
        self.assertEqual(annotated.available, 1)
        self.assertEqual(book.available_copies, 1)

    def test_returned_loan_frees_the_copy(self):
        make_loan(self.book, self.member, days_ago=3, kept=2)
        self.assertEqual(Book.objects.with_availability().get(pk=self.book.pk).available, 1)

    def test_spine_values_are_stable_and_in_range(self):
        self.assertTrue(0 <= self.book.spine_hue < 360)
        self.assertTrue(150 <= self.book.spine_height < 206)


# ===========================================================================
# Lending rules (services)
# ===========================================================================

class IssueRuleTests(LibraryTestCase):

    def test_issue_sets_due_date_from_loan_period(self):
        loan = issue_book(self.book, self.member, issued_by=self.librarian)
        self.assertEqual(loan.due_date, timezone.localdate() + timedelta(days=14))
        self.assertEqual(loan.issued_by, self.librarian)

    @override_settings(LOAN_PERIOD_DAYS=7)
    def test_loan_period_is_configurable(self):
        loan = issue_book(self.book, self.member)
        self.assertEqual((loan.due_date - loan.issued_on).days, 7)

    def test_cannot_issue_when_no_copy_is_left(self):
        make_loan(self.book, make_member('ravi'), days_ago=1)
        with self.assertRaisesMessage(LoanError, 'No copies'):
            issue_book(self.book, self.member)

    def test_cannot_borrow_same_book_twice(self):
        book = make_book('Two copies', copies=2)
        issue_book(book, self.member)
        with self.assertRaisesMessage(LoanError, 'already has'):
            issue_book(book, self.member)

    def test_active_loan_limit(self):
        for i in range(3):
            issue_book(make_book(f'Book {i}'), self.member)
        with self.assertRaisesMessage(LoanError, 'limit is 3'):
            issue_book(make_book('One too many'), self.member)

    def test_overdue_book_blocks_new_loans(self):
        make_loan(make_book('Late'), self.member, days_ago=20)
        with self.assertRaisesMessage(LoanError, 'overdue'):
            issue_book(self.book, self.member)

    def test_unpaid_fine_blocks_new_loans(self):
        make_loan(make_book('Was late'), self.member, days_ago=40, kept=20)
        with self.assertRaisesMessage(LoanError, 'unpaid fines of Rs. 12.00'):
            issue_book(self.book, self.member)

    def test_paid_fine_does_not_block(self):
        late = make_loan(make_book('Was late'), self.member, days_ago=40, kept=20)
        late.fine_paid = True
        late.save()
        self.assertEqual(issue_book(self.book, self.member).member, self.member)

    def test_librarians_cannot_borrow(self):
        with self.assertRaisesMessage(LoanError, 'Librarians'):
            issue_book(self.book, self.librarian)

    def test_member_standing_totals(self):
        make_loan(make_book('Late'), self.member, days_ago=18)                # 4 days = 8
        make_loan(make_book('Fresh'), self.member, days_ago=2)
        make_loan(make_book('Old'), self.member, days_ago=40, kept=20)         # 12 unpaid
        standing = member_standing(self.member)
        self.assertEqual(standing['active_count'], 2)
        self.assertEqual(standing['overdue_count'], 1)
        self.assertEqual(standing['unpaid_fines'], Decimal('12.00'))
        self.assertEqual(standing['accruing_fines'], Decimal('8.00'))
        self.assertEqual(standing['total_owed'], Decimal('20.00'))


# ===========================================================================
# Forms
# ===========================================================================

class BookFormTests(LibraryTestCase):

    def data(self, **extra):
        data = {'title': 'New Book', 'author': 'Someone', 'isbn': '978-0-13-235088-4',
                'total_copies': 2, 'published_year': 2020, 'description': ''}
        data.update(extra)
        return data

    def test_isbn_is_normalised(self):
        form = BookForm(self.data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['isbn'], '9780132350884')

    def test_isbn_with_x_check_digit_is_accepted(self):
        self.assertTrue(BookForm(self.data(isbn='080442957x')).is_valid())

    def test_invalid_isbn_rejected(self):
        self.assertFalse(BookForm(self.data(isbn='12345')).is_valid())

    def test_duplicate_isbn_rejected(self):
        make_book('Existing', isbn='9780132350884')
        self.assertIn('isbn', BookForm(self.data()).errors)

    def test_unrealistic_year_rejected(self):
        self.assertIn('published_year', BookForm(self.data(published_year=12)).errors)

    def test_new_category_is_created(self):
        form = BookForm(self.data(new_category='Poetry'))
        book = form.save() if form.is_valid() else self.fail(form.errors)
        self.assertEqual(book.category.name, 'Poetry')

    def test_new_category_reuses_existing_ignoring_case(self):
        existing = Category.objects.create(name='Poetry')
        form = BookForm(self.data(new_category='poetry'))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.save().category, existing)
        self.assertEqual(Category.objects.count(), 1)

    def test_cannot_reduce_copies_below_copies_on_loan(self):
        book = make_book('Popular', copies=3)
        make_loan(book, self.member, days_ago=1)
        make_loan(book, make_member('ravi'), days_ago=1)
        form = BookForm(self.data(isbn=book.isbn, total_copies=1), instance=book)
        self.assertIn('total_copies', form.errors)


class IssueFormTests(LibraryTestCase):

    def test_staff_and_unavailable_books_are_not_offered(self):
        out = make_book('All out')
        make_loan(out, self.member, days_ago=1)
        form = IssueBookForm()
        self.assertNotIn(out, form.fields['book'].queryset)
        self.assertIn(self.book, form.fields['book'].queryset)
        self.assertNotIn(self.librarian, form.fields['member'].queryset)
        self.assertIn(self.member, form.fields['member'].queryset)

    def test_rule_violation_becomes_a_form_error(self):
        make_loan(make_book('Late'), self.member, days_ago=30)
        form = IssueBookForm({'book': self.book.pk, 'member': self.member.pk})
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.save())
        self.assertIn('overdue', form.non_field_errors()[0])


# ===========================================================================
# Public catalogue
# ===========================================================================

class CatalogueViewTests(LibraryTestCase):

    def test_home_loads_empty_and_full(self):
        Book.objects.all().delete()
        self.assertContains(self.client.get(reverse('home')), 'The shelves are empty')
        make_book('Shelf Book')
        self.assertContains(self.client.get(reverse('home')), 'Shelf Book')

    def test_search_by_title_author_and_isbn(self):
        make_book('Django Unchained', author='Quentin T', isbn='9781111111111')
        url = reverse('book_list')
        self.assertContains(self.client.get(url, {'q': 'unchained'}), 'Django Unchained')
        self.assertNotContains(self.client.get(url, {'q': 'unchained'}), 'Clean Code')
        self.assertContains(self.client.get(url, {'q': 'quentin'}), 'Django Unchained')
        self.assertContains(self.client.get(url, {'q': '978-1111111111'}), 'Django Unchained')

    def test_category_filter_and_bad_value_ignored(self):
        cat = Category.objects.create(name='Poetry')
        make_book('Verses', category=cat)
        url = reverse('book_list')
        only = self.client.get(url, {'category': cat.pk})
        self.assertContains(only, 'Verses')
        self.assertNotContains(only, 'Clean Code')
        self.assertContains(self.client.get(url, {'category': 'abc'}), 'Clean Code')

    def test_available_only_filter(self):
        out = make_book('All out')
        make_loan(out, self.member, days_ago=1)
        response = self.client.get(reverse('book_list'), {'available': '1'})
        self.assertContains(response, 'Clean Code')
        self.assertNotContains(response, 'All out')

    def test_pagination_keeps_filters(self):
        for i in range(11):
            make_book(f'Widget {i}')
        page1 = self.client.get(reverse('book_list'), {'q': 'Widget'})
        self.assertEqual(len(page1.context['books']), 9)
        self.assertContains(page1, 'q=Widget&amp;page=2')
        self.assertEqual(len(self.client.get(reverse('book_list'), {'q': 'Widget', 'page': 2}).context['books']), 2)

    def test_book_detail_and_404(self):
        self.assertContains(self.client.get(self.book.get_absolute_url()), 'Clean Code')
        self.assertEqual(self.client.get(reverse('book_detail', args=[9999])).status_code, 404)

    def test_member_sees_their_own_loan_on_the_book_page(self):
        make_loan(self.book, self.member, days_ago=1)
        self.login(self.member)
        self.assertContains(self.client.get(self.book.get_absolute_url()), 'You have this book')

    def test_librarian_sees_loan_history_and_issue_button(self):
        book = make_book('Two', copies=2)
        make_loan(book, self.member, days_ago=1)
        self.login(self.librarian)
        response = self.client.get(book.get_absolute_url())
        self.assertContains(response, 'asha')
        self.assertContains(response, 'Issue this book')


# ===========================================================================
# Access control
# ===========================================================================

class AccessControlTests(LibraryTestCase):

    STAFF_URLS = [
        ('dashboard', []), ('issue_book', []), ('loan_list', []), ('export_loans', []),
        ('member_list', []), ('book_create', []),
    ]

    def staff_urls(self):
        return self.STAFF_URLS + [
            ('book_update', [self.book.pk]), ('book_delete', [self.book.pk]),
            ('member_detail', [self.member.pk]),
        ]

    def test_anonymous_users_are_sent_to_login(self):
        for name, args in self.staff_urls() + [('my_loans', [])]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse('login'), response['Location'], name)

    def test_members_cannot_open_librarian_pages(self):
        self.login(self.member)
        for name, args in self.staff_urls():
            response = self.client.get(reverse(name, args=args))
            self.assertRedirects(response, reverse('home'), fetch_redirect_response=False, msg_prefix=name)

    def test_members_cannot_change_data_by_posting(self):
        loan = make_loan(self.book, self.member, days_ago=1)
        self.login(self.member)
        self.client.post(reverse('return_loan', args=[loan.pk]))
        self.client.post(reverse('book_delete', args=[self.book.pk]))
        loan.refresh_from_db()
        self.assertFalse(loan.is_returned)
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())


# ===========================================================================
# Librarian: books
# ===========================================================================

class BookManagementTests(LibraryTestCase):

    def setUp(self):
        super().setUp()
        self.login(self.librarian)

    def test_create_book(self):
        response = self.client.post(reverse('book_create'), {
            'title': 'Fresh', 'author': 'A', 'isbn': '9782222222222', 'total_copies': 2,
        })
        book = Book.objects.get(title='Fresh')
        self.assertRedirects(response, book.get_absolute_url())

    def test_invalid_book_is_redisplayed(self):
        response = self.client.post(reverse('book_create'), {'title': '', 'isbn': 'x', 'total_copies': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Book.objects.count(), 1)

    def test_edit_book(self):
        self.client.post(reverse('book_update', args=[self.book.pk]), {
            'title': 'Clean Code, 2nd', 'author': 'Robert Martin', 'isbn': self.book.isbn, 'total_copies': 4,
        })
        self.book.refresh_from_db()
        self.assertEqual(self.book.title, 'Clean Code, 2nd')
        self.assertEqual(self.book.total_copies, 4)

    def test_delete_requires_post(self):
        self.assertContains(self.client.get(reverse('book_delete', args=[self.book.pk])), 'Delete this book?')
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())
        self.client.post(reverse('book_delete', args=[self.book.pk]))
        self.assertFalse(Book.objects.filter(pk=self.book.pk).exists())

    def test_book_with_history_cannot_be_deleted(self):
        make_loan(self.book, self.member, days_ago=30, kept=5)
        response = self.client.post(reverse('book_delete', args=[self.book.pk]), follow=True)
        self.assertContains(response, 'has loan history')
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())


# ===========================================================================
# Librarian: lending desk
# ===========================================================================

class LendingDeskTests(LibraryTestCase):

    def setUp(self):
        super().setUp()
        self.login(self.librarian)

    def test_issue_book_flow(self):
        response = self.client.post(reverse('issue_book'), {'book': self.book.pk, 'member': self.member.pk})
        self.assertRedirects(response, reverse('loan_list'))
        loan = Loan.objects.get()
        self.assertEqual((loan.book, loan.member, loan.issued_by), (self.book, self.member, self.librarian))

    def test_issue_book_prefills_from_query_string(self):
        response = self.client.get(reverse('issue_book'), {'book': self.book.pk})
        self.assertEqual(response.context['form'].initial['book'], str(self.book.pk))

    def test_issue_rule_error_is_shown_and_nothing_is_created(self):
        make_loan(make_book('Late'), self.member, days_ago=30)
        before = Loan.objects.count()
        response = self.client.post(reverse('issue_book'), {'book': self.book.pk, 'member': self.member.pk})
        self.assertContains(response, 'has overdue books')
        self.assertEqual(Loan.objects.count(), before)

    def test_return_on_time(self):
        loan = make_loan(self.book, self.member, days_ago=3)
        response = self.client.post(reverse('return_loan', args=[loan.pk]), follow=True)
        self.assertContains(response, 'returned on time')
        loan.refresh_from_db()
        self.assertTrue(loan.is_returned)
        self.assertEqual(loan.fine, 0)

    def test_late_return_reports_the_fine(self):
        loan = make_loan(self.book, self.member, days_ago=19)    # 5 days late
        response = self.client.post(reverse('return_loan', args=[loan.pk]), follow=True)
        self.assertContains(response, '5 day(s) late')
        self.assertContains(response, 'Rs. 10.00')
        loan.refresh_from_db()
        self.assertEqual(loan.fine, Decimal('10.00'))

    def test_returning_twice_does_nothing(self):
        loan = make_loan(self.book, self.member, days_ago=3, kept=2)
        returned_on = loan.returned_on
        response = self.client.post(reverse('return_loan', args=[loan.pk]), follow=True)
        self.assertContains(response, 'already returned')
        loan.refresh_from_db()
        self.assertEqual(loan.returned_on, returned_on)

    def test_return_rejects_get(self):
        loan = make_loan(self.book, self.member, days_ago=3)
        self.assertEqual(self.client.get(reverse('return_loan', args=[loan.pk])).status_code, 405)

    def test_return_redirects_back_only_to_safe_urls(self):
        loan = make_loan(self.book, self.member, days_ago=3)
        response = self.client.post(reverse('return_loan', args=[loan.pk]), {'next': 'https://evil.example.com/'})
        self.assertRedirects(response, reverse('loan_list'), fetch_redirect_response=False)
        loan2 = make_loan(make_book('B'), self.member, days_ago=3)
        response = self.client.post(reverse('return_loan', args=[loan2.pk]), {'next': '/desk/'})
        self.assertRedirects(response, '/desk/', fetch_redirect_response=False)

    def test_mark_fine_paid_unblocks_the_member(self):
        loan = make_loan(self.book, self.member, days_ago=40, kept=20)
        with self.assertRaises(LoanError):
            issue_book(make_book('Next'), self.member)
        self.client.post(reverse('mark_fine_paid', args=[loan.pk]))
        loan.refresh_from_db()
        self.assertTrue(loan.fine_paid)
        issue_book(make_book('Next 2'), self.member)

    def test_mark_fine_paid_without_a_fine_is_rejected(self):
        loan = make_loan(self.book, self.member, days_ago=5, kept=4)
        response = self.client.post(reverse('mark_fine_paid', args=[loan.pk]), follow=True)
        self.assertContains(response, 'no unpaid fine')

    def test_cannot_mark_fine_paid_before_return(self):
        loan = make_loan(self.book, self.member, days_ago=30)
        self.client.post(reverse('mark_fine_paid', args=[loan.pk]))
        loan.refresh_from_db()
        self.assertFalse(loan.fine_paid)


class LoanListAndMembersTests(LibraryTestCase):

    def setUp(self):
        super().setUp()
        self.ravi = make_member('ravi', first_name='Ravi', last_name='Menon')
        self.fresh = make_loan(make_book('Fresh Book'), self.member, days_ago=2)
        self.late = make_loan(make_book('Late Book'), self.member, days_ago=25)
        self.done = make_loan(make_book('Done Book'), self.ravi, days_ago=50, kept=10)
        self.owed = make_loan(make_book('Owed Book'), self.ravi, days_ago=60, kept=24)
        self.login(self.librarian)

    def titles(self, **params):
        response = self.client.get(reverse('loan_list'), params)
        return {loan.book.title for loan in response.context['loans']}

    def test_status_tabs(self):
        self.assertEqual(self.titles(status='active'), {'Fresh Book', 'Late Book'})
        self.assertEqual(self.titles(status='overdue'), {'Late Book'})
        self.assertEqual(self.titles(status='returned'), {'Done Book', 'Owed Book'})
        self.assertEqual(self.titles(status='unpaid'), {'Owed Book'})
        self.assertEqual(len(self.titles()), 4)
        self.assertEqual(len(self.titles(status='nonsense')), 4)

    def test_search_loans(self):
        self.assertEqual(self.titles(q='ravi'), {'Done Book', 'Owed Book'})
        self.assertEqual(self.titles(q='fresh'), {'Fresh Book'})

    def test_member_list_shows_counts_and_unpaid_fines(self):
        response = self.client.get(reverse('member_list'))
        rows = {m.username: m for m in response.context['members']}
        self.assertEqual(rows['asha'].on_loan, 2)
        self.assertEqual(rows['ravi'].on_loan, 0)
        self.assertEqual(rows['ravi'].unpaid, Decimal('20.00'))
        self.assertNotIn('lena', rows)        # librarians are not members

    def test_member_search(self):
        response = self.client.get(reverse('member_list'), {'q': 'menon'})
        self.assertEqual([m.username for m in response.context['members']], ['ravi'])

    def test_member_detail_and_librarian_404(self):
        response = self.client.get(reverse('member_detail', args=[self.ravi.pk]))
        self.assertContains(response, 'Owed Book')
        self.assertContains(response, 'Owes Rs. 20.00')
        self.assertEqual(self.client.get(reverse('member_detail', args=[self.librarian.pk])).status_code, 404)

    def test_csv_export(self):
        response = self.client.get(reverse('export_loans'), {'status': 'overdue'})
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment; filename="loans-', response['Content-Disposition'])
        lines = response.content.decode().strip().splitlines()
        self.assertEqual(lines[0].split(',')[0:3], ['Loan ID', 'Book', 'ISBN'])
        self.assertEqual(len(lines), 2)
        self.assertIn('Late Book', lines[1])
        self.assertIn('overdue', lines[1])

    def test_csv_neutralises_spreadsheet_formulas(self):
        make_loan(make_book('=HYPERLINK("http://evil")'), self.ravi, days_ago=1)
        body = self.client.get(reverse('export_loans')).content.decode()
        self.assertIn("'=HYPERLINK", body)

    def test_dashboard(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        stats = response.context['stats']
        self.assertEqual((stats['titles'], stats['members'], stats['on_loan'], stats['overdue']), (5, 2, 2, 1))
        self.assertEqual(stats['unpaid'], Decimal('20.00'))
        chart = response.context['chart']
        self.assertEqual(len(chart['months']), 6)
        self.assertEqual(sum(chart['loans']), Loan.objects.count())
        self.assertContains(response, 'Late Book')


# ===========================================================================
# Member pages
# ===========================================================================

class MyLoansTests(LibraryTestCase):

    def test_member_sees_only_their_own_loans(self):
        make_loan(self.book, self.member, days_ago=1)
        other = make_book('Not mine')
        make_loan(other, make_member('ravi'), days_ago=1)
        self.login(self.member)
        response = self.client.get(reverse('my_loans'))
        self.assertContains(response, 'Clean Code')
        self.assertNotContains(response, 'Not mine')

    def test_overdue_and_fine_messages(self):
        make_loan(self.book, self.member, days_ago=20)
        make_loan(make_book('Old'), self.member, days_ago=40, kept=20)
        self.login(self.member)
        response = self.client.get(reverse('my_loans'))
        self.assertContains(response, 'You owe Rs.')
        self.assertContains(response, 'Overdue 6 days')
        self.assertEqual(len(response.context['history']), 1)

    def test_empty_state(self):
        self.login(self.member)
        self.assertContains(self.client.get(reverse('my_loans')), 'You have no books out')


# ===========================================================================
# Demo data command
# ===========================================================================

class SeedDemoTests(TestCase):

    def test_seed_is_repeatable_and_creates_realistic_data(self):
        call_command('seed_demo', stdout=StringIO())
        call_command('seed_demo', stdout=StringIO())
        self.assertEqual(Book.objects.count(), 16)
        self.assertEqual(Loan.objects.count(), 14)
        self.assertEqual(Loan.objects.overdue().count(), 1)
        self.assertEqual(Loan.objects.unpaid_fines().count(), 1)
        self.assertTrue(User.objects.get(username='librarian').is_staff)
        self.assertTrue(User.objects.get(username='asha').check_password('member123'))
