# Shelfmark - Library Management System

A full-stack library management web app built with **Django** and **SQLite**.
Librarians manage the catalogue and lend books at the desk; members browse the
catalogue and track their own loans, due dates and fines.

## Features

**Catalogue (everyone)**
- Search by title, author or ISBN, filter by category and availability, paginated results
- Book pages showing live availability (copies on the shelf vs. on loan)

**Members**
- Self-registration, login, logout and password change
- "My loans" page with due-date stamps, overdue warnings and fines owed

**Librarians**
- Add, edit and delete books (ISBN validation, create categories on the fly)
- Issue and return books at the lending desk
- Loans list with status tabs (on loan, overdue, unpaid fines, returned) and search
- Member list with books out and unpaid fines
- Dashboard with key figures and Chart.js charts (books issued per month, titles by category)
- Download loans as CSV (spreadsheet-formula safe)

**Lending rules** (all enforced in one place, `library/services.py`)
- A loan lasts 14 days; each overdue day costs Rs. 2 (both configurable)
- A member can hold at most 3 books at once
- No second copy of the same book for the same member
- No borrowing while books are overdue or fines are unpaid
- Returning a late book freezes the fine; the librarian marks it paid
- A book with loan history cannot be deleted, so records stay intact

## Tech stack

Python, Django, SQLite, HTML, CSS, JavaScript, Chart.js

## Run it locally

```bash
git clone https://github.com/<your-username>/shelfmark.git
cd shelfmark

python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_demo          # optional: sample books, members and loans
python manage.py runserver
```

Open http://127.0.0.1:8000/

### Demo accounts (created by `seed_demo`)

| Role      | Username    | Password       |
|-----------|-------------|----------------|
| Librarian | `librarian` | `librarian123` |
| Member    | `asha`      | `member123`    |

`ravi`, `meera` and `kiran` are also members. Kiran has an unpaid fine, so you can
see the borrowing rule in action. To create your own librarian instead, run
`python manage.py createsuperuser`; superusers are staff, so they get the desk.

## Configuration

The lending rules live in `config/settings.py`:

```python
LOAN_PERIOD_DAYS = 14
FINE_PER_DAY = Decimal('2.00')
MAX_ACTIVE_LOANS = 3
```

For deployment, set these environment variables (never commit real secrets):

| Variable               | Purpose                                      |
|------------------------|----------------------------------------------|
| `DJANGO_SECRET_KEY`    | Secret key (required in production)          |
| `DJANGO_DEBUG`         | `0` to turn debug mode off                   |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames                    |

## Tests

```bash
python manage.py test
```

81 automated tests cover the models, lending rules, forms, permissions, fines,
CSV export, registration and login.

## Project structure

```
config/      project settings and root URLs
accounts/    registration, login, logout, password change
library/     books, loans, lending rules, librarian desk, member pages
  models.py        Category, Book, Loan (+ availability and fine logic)
  services.py      lending rules: issue_book(), member_standing()
  views.py         catalogue, desk, dashboard, CSV export
  management/      seed_demo command
templates/   base layout and shared template pieces
static/css/  stylesheet
```

## License

MIT
