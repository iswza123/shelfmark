import csv
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import staff_required
from .forms import BookForm, IssueBookForm
from .models import Book, Category, Loan
from .services import member_standing

User = get_user_model()

BOOKS_PER_PAGE = 9
LOANS_PER_PAGE = 15


def _paginate(request, queryset, per_page):
    page = Paginator(queryset, per_page).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return page, params.urlencode()


def _safe_next(request, fallback):
    target = request.POST.get('next', '')
    ok = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    )
    return target if ok else fallback


# ---------------------------------------------------------------------------
# Public catalogue
# ---------------------------------------------------------------------------

def home(request):
    shelf = list(Book.objects.with_availability().order_by('-added_on')[:11])

    return render(request, 'library/home.html', {
        'shelf': shelf,
        'new_books': shelf[:5],
        'title_count': Book.objects.count(),
        'copy_count': Book.objects.aggregate(t=Sum('total_copies'))['t'] or 0,
        'categories': Category.objects.annotate(n=Count('books')).filter(n__gt=0),
    })


def book_list(request):
    books = Book.objects.with_availability().select_related('category')

    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', '')
    only_available = request.GET.get('available') == '1'

    if q:
        books = books.filter(
            Q(title__icontains=q) | Q(author__icontains=q) | Q(isbn__icontains=q.replace('-', ''))
        )

    if category.isdigit():
        books = books.filter(category_id=int(category))
    else:
        category = ''

    if only_available:
        books = books.filter(available__gt=0)

    page, querystring = _paginate(request, books.order_by('title'), BOOKS_PER_PAGE)

    return render(request, 'library/book_list.html', {
        'page_obj': page,
        'books': page.object_list,
        'querystring': querystring,
        'q': q,
        'selected_category': category,
        'only_available': only_available,
        'categories': Category.objects.all(),
    })


def book_detail(request, pk):
    book = get_object_or_404(
        Book.objects.with_availability().select_related('category'), pk=pk,
    )

    context = {'book': book}

    if request.user.is_authenticated and request.user.is_staff:
        context['history'] = book.loans.select_related('member')[:15]
    elif request.user.is_authenticated:
        context['my_loan'] = book.loans.active().filter(member=request.user).first()

    return render(request, 'library/book_detail.html', context)


# ---------------------------------------------------------------------------
# Librarian: manage books
# ---------------------------------------------------------------------------

@staff_required
def book_create(request):
    form = BookForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        book = form.save()
        messages.success(request, f'"{book.title}" was added to the catalogue.')
        return redirect(book)

    return render(request, 'library/book_form.html', {'form': form, 'heading': 'Add a book'})


@staff_required
def book_update(request, pk):
    book = get_object_or_404(Book, pk=pk)
    form = BookForm(request.POST or None, instance=book)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'"{book.title}" was updated.')
        return redirect(book)

    return render(request, 'library/book_form.html', {
        'form': form, 'heading': f'Edit "{book.title}"', 'book': book,
    })


@staff_required
def book_delete(request, pk):
    book = get_object_or_404(Book, pk=pk)

    if request.method == 'POST':
        try:
            book.delete()
        except ProtectedError:
            messages.error(
                request,
                f'"{book.title}" has loan history, so it cannot be deleted.',
            )
            return redirect(book)

        messages.success(request, f'"{book.title}" was deleted.')
        return redirect('book_list')

    return render(request, 'library/book_confirm_delete.html', {
        'book': book, 'loan_count': book.loans.count(),
    })


# ---------------------------------------------------------------------------
# Librarian: lending desk
# ---------------------------------------------------------------------------

@staff_required
def issue_book_view(request):
    form = IssueBookForm(
        request.POST or None,
        initial={'book': request.GET.get('book')},
        issued_by=request.user,
    )

    if request.method == 'POST' and form.is_valid():
        loan = form.save()

        if loan:
            messages.success(
                request,
                f'Issued "{loan.book.title}" to {loan.member.username}, '
                f'due {loan.due_date:%d %b %Y}.',
            )
            return redirect('loan_list')

    return render(request, 'library/issue_book.html', {'form': form})


@staff_required
@require_POST
def return_loan(request, pk):
    loan = get_object_or_404(Loan.objects.select_related('book', 'member'), pk=pk)

    if loan.is_returned:
        messages.info(request, 'That book was already returned.')
    else:
        loan.return_book()

        if loan.fine > 0:
            messages.warning(
                request,
                f'"{loan.book.title}" returned {loan.days_overdue} day(s) late. '
                f'Fine: Rs. {loan.fine}.',
            )
        else:
            messages.success(request, f'"{loan.book.title}" returned on time.')

    return redirect(_safe_next(request, 'loan_list'))


@staff_required
@require_POST
def mark_fine_paid(request, pk):
    loan = get_object_or_404(Loan.objects.select_related('member'), pk=pk)

    if loan.is_returned and loan.fine > 0 and not loan.fine_paid:
        loan.fine_paid = True
        loan.save(update_fields=['fine_paid'])
        messages.success(request, f'Fine of Rs. {loan.fine} marked as paid.')
    else:
        messages.error(request, 'There is no unpaid fine on that loan.')

    return redirect(_safe_next(request, 'loan_list'))


def _filtered_loans(request):
    loans = Loan.objects.select_related('book', 'member')

    status = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()

    if status == 'active':
        loans = loans.active()
    elif status == 'overdue':
        loans = loans.overdue()
    elif status == 'returned':
        loans = loans.returned()
    elif status == 'unpaid':
        loans = loans.unpaid_fines()
    else:
        status = ''

    if q:
        loans = loans.filter(
            Q(member__username__icontains=q) | Q(book__title__icontains=q) |
            Q(book__isbn__icontains=q.replace('-', ''))
        )

    return loans, status, q


@staff_required
def loan_list(request):
    loans, status, q = _filtered_loans(request)
    page, querystring = _paginate(request, loans, LOANS_PER_PAGE)

    return render(request, 'library/loan_list.html', {
        'page_obj': page, 'loans': page.object_list, 'querystring': querystring,
        'status': status, 'q': q,
        'statuses': [
            ('', 'All'), ('active', 'On loan'), ('overdue', 'Overdue'),
            ('unpaid', 'Unpaid fines'), ('returned', 'Returned'),
        ],
    })


def _csv_safe(value):
    """Stop spreadsheet apps from running cell text as a formula."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


@staff_required
def export_loans_csv(request):
    loans, _, _ = _filtered_loans(request)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="loans-{timezone.localdate():%Y-%m-%d}.csv"'
    )

    writer = csv.writer(response)
    writer.writerow([
        'Loan ID', 'Book', 'ISBN', 'Member', 'Issued on', 'Due date',
        'Returned on', 'Status', 'Fine (Rs.)', 'Fine paid',
    ])

    for loan in loans:
        writer.writerow([_csv_safe(v) for v in (
            loan.pk, loan.book.title, loan.book.isbn, loan.member.username,
            loan.issued_on, loan.due_date, loan.returned_on or '',
            loan.status, loan.current_fine, 'yes' if loan.fine_paid else 'no',
        )])

    return response


# ---------------------------------------------------------------------------
# Librarian: members + dashboard
# ---------------------------------------------------------------------------

@staff_required
def member_list(request):
    q = request.GET.get('q', '').strip()

    members = User.objects.filter(is_staff=False).annotate(
        on_loan=Count('loans', filter=Q(loans__returned_on__isnull=True)),
        unpaid=Sum('loans__fine', filter=Q(loans__returned_on__isnull=False, loans__fine_paid=False)),
    ).order_by('username')

    if q:
        members = members.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(email__icontains=q)
        )

    page, querystring = _paginate(request, members, LOANS_PER_PAGE)

    return render(request, 'library/member_list.html', {
        'page_obj': page, 'members': page.object_list, 'querystring': querystring, 'q': q,
    })


@staff_required
def member_detail(request, pk):
    member = get_object_or_404(User, pk=pk, is_staff=False)

    return render(request, 'library/member_detail.html', {
        'member': member,
        'standing': member_standing(member),
        'loans': member.loans.select_related('book'),
    })


def _last_six_months():
    today = timezone.localdate()
    months, year, month = [], today.year, today.month

    for _ in range(6):
        months.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12

    return list(reversed(months))


@staff_required
def dashboard(request):
    months = _last_six_months()

    per_month = {
        row['m'].date() if hasattr(row['m'], 'date') else row['m']: row['n']
        for row in Loan.objects.filter(issued_on__gte=months[0])
        .annotate(m=TruncMonth('issued_on')).values('m').annotate(n=Count('id'))
    }

    by_category = list(
        Category.objects.annotate(n=Count('books')).filter(n__gt=0).order_by('-n')
        .values_list('name', 'n')
    )

    overdue = Loan.objects.overdue().select_related('book', 'member').order_by('due_date')

    return render(request, 'library/dashboard.html', {
        'stats': {
            'titles': Book.objects.count(),
            'members': User.objects.filter(is_staff=False).count(),
            'on_loan': Loan.objects.active().count(),
            'overdue': overdue.count(),
            'unpaid': (Loan.objects.unpaid_fines().aggregate(t=Sum('fine'))['t'] or Decimal('0')).quantize(Decimal('0.01')),
        },
        'overdue_loans': overdue[:8],
        'recent_loans': Loan.objects.select_related('book', 'member')[:6],
        'chart': {
            'months': [m.strftime('%b %Y') for m in months],
            'loans': [per_month.get(m, 0) for m in months],
            'categories': [name for name, _ in by_category],
            'category_counts': [n for _, n in by_category],
        },
    })


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

@login_required
def my_loans(request):
    loans = request.user.loans.select_related('book')

    return render(request, 'library/my_loans.html', {
        'standing': member_standing(request.user),
        'current': [loan for loan in loans if not loan.is_returned],
        'history': [loan for loan in loans if loan.is_returned],
    })


@login_required
def post_login(request):
    return redirect('dashboard' if request.user.is_staff else 'my_loans')
