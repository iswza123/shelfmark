import re

from django import forms
from django.contrib.auth import get_user_model

from .models import Book, Category
from .services import LoanError, issue_book

User = get_user_model()


class BookForm(forms.ModelForm):
    # Wider than the model's 13 so "978-0-13-235088-4" can be typed as printed;
    # clean_isbn() strips the separators before the model's limit is checked.
    isbn = forms.CharField(max_length=20, label='ISBN', help_text='10 or 13 digits. Hyphens are fine.')

    new_category = forms.CharField(
        required=False, max_length=80, label='Or add a new category',
    )

    class Meta:
        model = Book
        fields = [
            'title', 'author', 'isbn', 'category', 'new_category',
            'published_year', 'total_copies', 'description',
        ]
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}

    field_order = Meta.fields

    def clean_isbn(self):
        isbn = re.sub(r'[\s-]', '', self.cleaned_data['isbn']).upper()

        if not re.fullmatch(r'\d{13}|\d{9}[\dX]', isbn):
            raise forms.ValidationError('Enter a valid 10 or 13 digit ISBN.')

        return isbn

    def clean_published_year(self):
        year = self.cleaned_data.get('published_year')

        if year is not None and not (1000 <= year <= 2100):
            raise forms.ValidationError('Enter a realistic year.')

        return year

    def clean_total_copies(self):
        copies = self.cleaned_data['total_copies']

        if self.instance.pk:
            out = self.instance.loans.filter(returned_on__isnull=True).count()

            if copies < out:
                raise forms.ValidationError(
                    f'{out} cop{"y is" if out == 1 else "ies are"} currently on loan, '
                    f'so there must be at least {out}.'
                )

        return copies

    def save(self, commit=True):
        book = super().save(commit=False)
        new_name = self.cleaned_data.get('new_category', '').strip()

        if new_name:
            category = Category.objects.filter(name__iexact=new_name).first()
            book.category = category or Category.objects.create(name=new_name)

        if commit:
            book.save()

        return book


class MemberChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        name = user.get_full_name()
        return f'{name} ({user.username})' if name else user.username


class IssueBookForm(forms.Form):
    book = forms.ModelChoiceField(queryset=Book.objects.none())
    member = MemberChoiceField(queryset=User.objects.none())

    def __init__(self, *args, issued_by=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.issued_by = issued_by

        self.fields['book'].queryset = (
            Book.objects.with_availability().filter(available__gt=0).order_by('title')
        )
        self.fields['member'].queryset = (
            User.objects.filter(is_staff=False, is_active=True).order_by('username')
        )

    def save(self):
        """Issue the book. Rule violations become normal form errors."""
        try:
            return issue_book(
                self.cleaned_data['book'],
                self.cleaned_data['member'],
                issued_by=self.issued_by,
            )
        except LoanError as error:
            self.add_error(None, str(error))
            return None
