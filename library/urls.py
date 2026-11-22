from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('post-login/', views.post_login, name='post_login'),

    # Catalogue
    path('books/', views.book_list, name='book_list'),
    path('books/add/', views.book_create, name='book_create'),
    path('books/<int:pk>/', views.book_detail, name='book_detail'),
    path('books/<int:pk>/edit/', views.book_update, name='book_update'),
    path('books/<int:pk>/delete/', views.book_delete, name='book_delete'),

    # Members
    path('my-loans/', views.my_loans, name='my_loans'),

    # Librarian desk
    path('desk/', views.dashboard, name='dashboard'),
    path('desk/issue/', views.issue_book_view, name='issue_book'),
    path('desk/loans/', views.loan_list, name='loan_list'),
    path('desk/loans/export/', views.export_loans_csv, name='export_loans'),
    path('desk/loans/<int:pk>/return/', views.return_loan, name='return_loan'),
    path('desk/loans/<int:pk>/fine-paid/', views.mark_fine_paid, name='mark_fine_paid'),
    path('desk/members/', views.member_list, name='member_list'),
    path('desk/members/<int:pk>/', views.member_detail, name='member_detail'),
]
