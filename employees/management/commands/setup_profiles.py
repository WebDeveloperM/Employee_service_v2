from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from employees.models import ROLE_ADMIN, UserProfile


class Command(BaseCommand):
    help = 'Create UserProfile for existing users that do not have one'

    def handle(self, *args, **options):
        users_without_profile = User.objects.filter(profile__isnull=True)
        count = 0
        for user in users_without_profile:
            role = ROLE_ADMIN if user.is_superuser else 'user'
            UserProfile.objects.create(user=user, role=role)
            count += 1
        self.stdout.write(self.style.SUCCESS(f'Created {count} profile(s)'))
