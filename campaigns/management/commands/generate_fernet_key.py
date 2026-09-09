from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Prints a new random key for the FERNET_KEY setting in .env"

    def handle(self, *args, **options):
        self.stdout.write(Fernet.generate_key().decode())
