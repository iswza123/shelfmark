from django.contrib import admin

from .models import Book, Category, Loan


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ('name',)


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'isbn', 'category', 'total_copies')
    list_filter = ('category',)
    search_fields = ('title', 'author', 'isbn')


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ('book', 'member', 'issued_on', 'due_date', 'returned_on', 'fine', 'fine_paid')
    list_filter = ('fine_paid',)
    search_fields = ('book__title', 'member__username')
    autocomplete_fields = ('book',)
