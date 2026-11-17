"""Add the supplied Knovista team roster to any configured Django database."""
import re
import unicodedata

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from Ems_app.models import Department, Employee


COMPANY_EMAIL_DOMAIN = 'knovistatechnologies.com'

# Names and roles are normalized from the roster supplied by HR. Existing
# employees with the same full name are left untouched when the command runs.
TEAM_ROSTER = [
    ('Sreehari', 'R', 'Tech Department (Technical Department)', 'UI/UX Trainer'),
    ('Nithin', 'Prathapan', 'Tech Department (Technical Department)', 'MERN Stack Trainer'),
    ('Jesbin', 'Thomas', 'Tech Department (Technical Department)', 'Accounts Trainer'),
    ('Parvathy', 'Raju', 'Tech Department (Technical Department)', 'Sr Python Developer'),
    ('Deepthy', 'E P', 'Tech Department (Technical Department)', 'Communication Trainer'),
    ('Chinchina', 'Lalu', 'Placement', 'Placement Supporting Officer'),
    ('Akhila', 'Subash V', 'Placement', 'Placement Officer'),
    ('Namitha', 'C', 'Placement', 'HR Executive'),
    ('Radhika', 'KJ', 'Tech Department (Technical Department)', 'Data Analytics Trainer'),
    ('Riya', 'Roy', 'Tech Department (Technical Department)', 'Data Analytics Trainer'),
    ('Sreerag', 'M S', 'Tech Department (Technical Department)', 'Sr Data Analytics Trainer'),
    ('Sreeraj', 'T', 'Tech Department (Technical Department)', 'Jr Data Analytics Trainer'),
    ('Gautham K', 'Sasidharan', 'Tech Department (Technical Department)', 'Data Analytics Specialist'),
    ('Anandhu', '', 'Content Creators', 'Content Creator'),
    ('Nandhu', '', 'Content Creators', 'Digital Marketing Specialist'),
    ('Srrechanthu', '', 'Content Creators', 'Content Creator'),
    ('Rifan', '', 'Content Creators', 'Head of YouTube Videos'),
    ('Joshy', '', 'Content Creators', 'Head of Content Creators'),
    ('Akhil', '', 'Sales', 'Sr BDE'),
    ('Riswana', '', 'Sales', 'BDE'),
    ('Megha', '', 'Sales', 'BDE'),
    ('Siju', '', 'Sales', 'BDE Manager'),
    ('Janasuthii', '', 'Sales', 'Data Analytics Specialist'),
    ('Basith', '', 'Accounting', 'Sr Account Manager'),
    ('Adithyan', '', 'Accounting', 'Jr Account Manager'),
    ('Gopika', '', 'Quality (QA)', 'Jr Quality Manager'),
    ('Jai On', '', 'Quality (QA)', 'Sr Quality Manager'),
]


def _email_part(value):
    normalized = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '.', normalized.lower()).strip('.')


class Command(BaseCommand):
    help = 'Seed the supplied team roster (existing employees are skipped by full name).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview records without writing to the database.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        dry_run = options['dry_run']
        created = 0
        skipped = 0
        missing_departments = set()

        for first_name, last_name, department_name, designation in TEAM_ROSTER:
            full_name = f'{first_name} {last_name}'.strip()
            if Employee.objects.filter(emp_name__iexact=full_name).exists():
                self.stdout.write(f'SKIP  {full_name} (already listed)')
                skipped += 1
                continue

            department = Department.objects.filter(dept_name__iexact=department_name).first()
            if not department:
                if dry_run:
                    if department_name not in missing_departments:
                        self.stdout.write(f'CREATE DEPARTMENT  {department_name}')
                        missing_departments.add(department_name)
                else:
                    department, _ = Department.objects.get_or_create(dept_name=department_name)

            base_email = f'{_email_part(first_name)}.{_email_part(last_name)}'.strip('.')
            email = f'{base_email}@{COMPANY_EMAIL_DOMAIN}'
            suffix = 2
            while User.objects.filter(email__iexact=email).exists() or Employee.objects.filter(email__iexact=email).exists():
                email = f'{base_email}{suffix}@{COMPANY_EMAIL_DOMAIN}'
                suffix += 1

            if dry_run:
                self.stdout.write(f'ADD   {full_name} | {department_name} | {designation} | {email} | {today}')
            else:
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=None,
                    first_name=first_name,
                    last_name=last_name,
                )
                user.profile.role = 'employee'
                user.profile.save(update_fields=['role'])
                Employee.objects.create(
                    user=user,
                    first_name=first_name,
                    last_name=last_name,
                    emp_name=full_name,
                    email=email,
                    department=department,
                    joining_date=today,
                    designation=designation,
                    location='Operational Office',
                    current_salary=0,
                    status='active',
                )
            created += 1

        action = 'would be added' if dry_run else 'added'
        self.stdout.write(self.style.SUCCESS(
            f'{created} employee record(s) {action}; {skipped} existing record(s) skipped.'
        ))
        if created and not dry_run:
            self.stdout.write(
                'Accounts have unusable passwords until HR sets individual employee credentials.'
            )
