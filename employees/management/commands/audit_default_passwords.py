"""Find accounts that still have the old default password "changeme123".

Before the security fix, an account created with a login but without a password got "changeme123"
(employees/serializers.py). Anyone could log in to such an account.

    python manage.py audit_default_passwords          # list them
    python manage.py audit_default_passwords --lock   # also make that password stop working

--lock sets an unusable password; the administrator/HR then gives the employee a new one
(employee card → "Янги парол"). Checking is slow on purpose (PBKDF2): ~0.2-0.5 s per account.
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from employees.models import Employee


OLD_DEFAULT_PASSWORDS = ('changeme123',)


class Command(BaseCommand):
    help = 'List (and optionally lock) accounts whose password is the old default "changeme123".'

    def add_arguments(self, parser):
        parser.add_argument('--lock', action='store_true', help='Set an unusable password on the found accounts.')

    def handle(self, *args, lock=False, **options):
        users = User.objects.filter(is_active=True).exclude(password__startswith='!').order_by('username')
        total = users.count()
        found = []
        for index, user in enumerate(users.iterator(), start=1):
            if any(user.check_password(candidate) for candidate in OLD_DEFAULT_PASSWORDS):
                found.append(user)
                self.stdout.write(self.style.WARNING(f'  default password: {user.username}'))
            if index % 100 == 0:
                self.stdout.write(f'  ... {index}/{total}')

        if lock:
            for user in found:
                user.set_unusable_password()
                user.save(update_fields=['password'])
                Employee.objects.filter(user=user).update(password_hash='')
        verb = 'locked' if lock else 'found'
        self.stdout.write(self.style.SUCCESS(f'Checked {total} active accounts, {verb} {len(found)} with the default password.'))
