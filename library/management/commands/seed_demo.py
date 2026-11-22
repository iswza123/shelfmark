"""Fill the database with sample data so the app can be explored straight away.

    python manage.py seed_demo

Safe to run more than once: existing books and users are left alone and the
sample loans are only created when there are no loans yet.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from library.models import Book, Category, Loan

User = get_user_model()

LIBRARIAN = ('librarian', 'librarian123')
MEMBERS = [
    ('asha', 'Asha', 'Nair'),
    ('ravi', 'Ravi', 'Menon'),
    ('meera', 'Meera', 'Das'),
    ('kiran', 'Kiran', 'Thomas'),
]
MEMBER_PASSWORD = 'member123'

# title, author, isbn, category, year, copies
BOOKS = [
    ('Clean Code', 'Robert C. Martin', '9780132350884', 'Programming', 2008, 3),
    ('The Pragmatic Programmer', 'David Thomas, Andrew Hunt', '9780135957059', 'Programming', 2019, 2),
    ('Fluent Python', 'Luciano Ramalho', '9781492056355', 'Programming', 2022, 2),
    ('Python Crash Course', 'Eric Matthes', '9781718502703', 'Programming', 2023, 4),
    ('Automate the Boring Stuff with Python', 'Al Sweigart', '9781593279929', 'Programming', 2019, 2),
    ('Code Complete', 'Steve McConnell', '9780735619678', 'Programming', 2004, 1),
    ('Introduction to Algorithms', 'Cormen, Leiserson, Rivest, Stein', '9780262046305', 'Computer Science', 2022, 2),
    ('Designing Data-Intensive Applications', 'Martin Kleppmann', '9781449373320', 'Computer Science', 2017, 2),
    ('Pride and Prejudice', 'Jane Austen', '9780141439518', 'Fiction', 1813, 3),
    ('1984', 'George Orwell', '9780451524935', 'Fiction', 1949, 3),
    ('To Kill a Mockingbird', 'Harper Lee', '9780061120084', 'Fiction', 1960, 2),
    ('The Alchemist', 'Paulo Coelho', '9780062315007', 'Fiction', 1988, 2),
    ('A Brief History of Time', 'Stephen Hawking', '9780553380163', 'Science', 1988, 2),
    ('Sapiens', 'Yuval Noah Harari', '9780062316097', 'Science', 2011, 2),
    ('Atomic Habits', 'James Clear', '9780735211292', 'Self-Help', 2018, 3),
    ('Wings of Fire', 'A. P. J. Abdul Kalam', '9788173711466', 'Biography', 1999, 2),
]


class Command(BaseCommand):
    help = 'Create sample books, members and loans for trying out Shelfmark.'

    def handle(self, *args, **options):
        self._users()
        self._books()
        self._loans()

        self.stdout.write(self.style.SUCCESS('\nSample data is ready.'))
        self.stdout.write(f'  Librarian : {LIBRARIAN[0]} / {LIBRARIAN[1]}')
        self.stdout.write(f'  Members   : asha, ravi, meera, kiran / {MEMBER_PASSWORD}')

    def _users(self):
        user, created = User.objects.get_or_create(
            username=LIBRARIAN[0],
            defaults={'is_staff': True, 'first_name': 'Lena', 'last_name': 'George',
                      'email': 'librarian@example.com'},
        )
        if created:
            user.set_password(LIBRARIAN[1])
            user.save()

        for username, first, last in MEMBERS:
            member, created = User.objects.get_or_create(
                username=username,
                defaults={'first_name': first, 'last_name': last,
                          'email': f'{username}@example.com'},
            )
            if created:
                member.set_password(MEMBER_PASSWORD)
                member.save()

    def _books(self):
        for title, author, isbn, category, year, copies in BOOKS:
            cat, _ = Category.objects.get_or_create(name=category)
            Book.objects.get_or_create(isbn=isbn, defaults={
                'title': title, 'author': author, 'category': cat,
                'published_year': year, 'total_copies': copies,
                'description': f'Sample catalogue entry for "{title}".',
            })

    def _loans(self):
        if Loan.objects.exists():
            return

        today = timezone.localdate()
        desk = User.objects.get(username=LIBRARIAN[0])
        book = lambda isbn: Book.objects.get(isbn=isbn)
        user = lambda name: User.objects.get(username=name)

        def loan(who, isbn, days_ago, returned_after=None):
            issued = today - timedelta(days=days_ago)
            record = Loan.objects.create(
                book=book(isbn), member=user(who), issued_by=desk,
                issued_on=issued, due_date=issued + timedelta(days=14),
            )
            if returned_after is not None:
                record.return_book(on=issued + timedelta(days=returned_after))
            return record

        # Currently out: one overdue, one due soon, one fresh.
        loan('asha', '9780132350884', 25)
        loan('asha', '9781718502703', 4)
        loan('ravi', '9780451524935', 10)

        # History spread over several months (feeds the dashboard chart).
        history = [
            ('asha', '9780141439518', 150, 12), ('ravi', '9780062315007', 130, 14),
            ('meera', '9780735211292', 110, 10), ('kiran', '9781593279929', 95, 13),
            ('ravi', '9780062316097', 80, 9), ('meera', '9780553380163', 70, 14),
            ('asha', '9781492056355', 55, 11), ('kiran', '9780061120084', 40, 12),
            ('meera', '9781449373320', 30, 8),
        ]
        for who, isbn, days_ago, kept in history:
            loan(who, isbn, days_ago, returned_after=kept)

        # Returned late: Kiran owes a fine that is still unpaid.
        loan('kiran', '9780262046305', 45, returned_after=20)
        # Returned late and already settled.
        late = loan('meera', '9788173711466', 60, returned_after=18)
        late.fine_paid = True
        late.save(update_fields=['fine_paid'])
