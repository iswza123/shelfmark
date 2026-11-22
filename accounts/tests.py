from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()
PASSWORD = 'StrongPass!234'


class RegistrationTests(TestCase):

    def payload(self, **extra):
        data = {'username': 'newbie', 'first_name': 'New', 'last_name': 'Reader',
                'email': 'new@example.com', 'password1': 'Zx9!kLm2#Qw', 'password2': 'Zx9!kLm2#Qw'}
        data.update(extra)
        return data

    def test_register_creates_a_normal_member_and_logs_in(self):
        response = self.client.post(reverse('register'), self.payload(), follow=True)
        user = User.objects.get(username='newbie')
        self.assertFalse(user.is_staff)
        self.assertRedirects(response, reverse('my_loans'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_registering_cannot_create_a_librarian(self):
        self.client.post(reverse('register'), self.payload(is_staff='on', is_superuser='on'))
        user = User.objects.get(username='newbie')
        self.assertFalse(user.is_staff or user.is_superuser)

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user('taken', 'NEW@example.com', PASSWORD)
        response = self.client.post(reverse('register'), self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_weak_password_is_rejected(self):
        self.client.post(reverse('register'), self.payload(password1='12345678', password2='12345678'))
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_logged_in_user_is_redirected_away(self):
        User.objects.create_user('asha', 'a@example.com', PASSWORD)
        self.client.login(username='asha', password=PASSWORD)
        self.assertRedirects(self.client.get(reverse('register')), reverse('post_login'),
                             fetch_redirect_response=False)


class LoginTests(TestCase):

    def setUp(self):
        User.objects.create_user('asha', 'a@example.com', PASSWORD)
        User.objects.create_user('lena', 'l@example.com', PASSWORD, is_staff=True)

    def login(self, username, **extra):
        return self.client.post(reverse('login'), {'username': username, 'password': PASSWORD, **extra})

    def test_member_lands_on_my_loans(self):
        self.assertRedirects(self.login('asha'), reverse('post_login'), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse('post_login')), reverse('my_loans'),
                             fetch_redirect_response=False)

    def test_librarian_lands_on_the_desk(self):
        self.login('lena')
        self.assertRedirects(self.client.get(reverse('post_login')), reverse('dashboard'),
                             fetch_redirect_response=False)

    def test_wrong_password(self):
        response = self.client.post(reverse('login'), {'username': 'asha', 'password': 'nope'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_next_is_followed_but_external_urls_are_not(self):
        self.assertRedirects(self.login('asha', next='/books/'), '/books/', fetch_redirect_response=False)
        self.client.logout()
        response = self.login('asha', next='https://evil.example.com/')
        self.assertNotIn('evil.example.com', response['Location'])

    def test_logout_needs_post(self):
        self.login('asha')
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)
        self.client.post(reverse('logout'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_change_password(self):
        self.login('asha')
        response = self.client.post(reverse('password_change'), {
            'old_password': PASSWORD, 'new_password1': 'An0therStrong!pass', 'new_password2': 'An0therStrong!pass',
        })
        self.assertRedirects(response, reverse('post_login'), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(username='asha').check_password('An0therStrong!pass'))

    def test_password_change_requires_login(self):
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 302)
