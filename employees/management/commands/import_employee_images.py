from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from employees.models import Employee


class Command(BaseCommand):
    help = 'Import employee base images from a folder where each filename matches the employee tabel number.'
    supported_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}

    def add_arguments(self, parser):
        parser.add_argument('images_dir', help='Path to the folder with employee images.')
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Replace existing employee photos if they are already set.',
        )
        parser.add_argument(
            '--recursive',
            action='store_true',
            help='Scan subfolders recursively.',
        )

    def handle(self, *args, **options):
        images_dir = Path(options['images_dir']).expanduser().resolve()
        overwrite = bool(options['overwrite'])
        recursive = bool(options['recursive'])

        if not images_dir.exists():
            raise CommandError(f'Folder not found: {images_dir}')
        if not images_dir.is_dir():
            raise CommandError(f'Path is not a folder: {images_dir}')

        iterator = images_dir.rglob('*') if recursive else images_dir.iterdir()
        image_files = [path for path in iterator if path.is_file() and path.suffix.lower() in self.supported_extensions]

        if not image_files:
            self.stdout.write(self.style.WARNING('No supported image files found.'))
            return

        imported_count = 0
        skipped_existing_count = 0
        missing_employee_count = 0

        for image_path in sorted(image_files):
            tabel_number = image_path.stem.strip()
            if not tabel_number:
                self.stdout.write(self.style.WARNING(f'Skipped file without tabel number in name: {image_path.name}'))
                continue

            employee = Employee.objects.filter(tabel_number=tabel_number, is_deleted=False).first()
            if employee is None:
                missing_employee_count += 1
                self.stdout.write(self.style.WARNING(f'Employee not found for tabel number {tabel_number}: {image_path.name}'))
                continue

            if employee.base_image and not overwrite:
                skipped_existing_count += 1
                self.stdout.write(self.style.WARNING(f'Skipped {tabel_number}: photo already exists. Use --overwrite to replace it.'))
                continue

            if employee.base_image and overwrite:
                employee.base_image.delete(save=False)

            employee.base_image.save(
                f'{tabel_number}{image_path.suffix.lower()}',
                ContentFile(image_path.read_bytes()),
                save=True,
            )
            imported_count += 1
            self.stdout.write(self.style.SUCCESS(f'Imported photo for {tabel_number}'))

        self.stdout.write(
            self.style.SUCCESS(
                'Employee image import finished. '
                f'Imported: {imported_count}, '
                f'Skipped existing: {skipped_existing_count}, '
                f'Employees not found: {missing_employee_count}.'
            )
        )