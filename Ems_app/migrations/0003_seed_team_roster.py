"""Seed the supplied employee roster in databases when migrations are applied."""
import re
import unicodedata
from datetime import date

from django.conf import settings
from django.db import migrations
from django.contrib.auth.hashers import make_password


ROSTER = [
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
    ascii_value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '.', ascii_value.lower()).strip('.')


def seed_team_roster(apps, schema_editor):
    Employee = apps.get_model('Ems_app', 'Employee')
    Department = apps.get_model('Ems_app', 'Department')
    UserProfile = apps.get_model('Accounts', 'UserProfile')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    database = schema_editor.connection.alias
    joining_date = date(2026, 11, 17)

    for first_name, last_name, department_name, designation in ROSTER:
        full_name = f'{first_name} {last_name}'.strip()
        if Employee.objects.using(database).filter(emp_name__iexact=full_name).exists():
            continue

        department, _ = Department.objects.using(database).get_or_create(dept_name=department_name)
        base_email = f'{_email_part(first_name)}.{_email_part(last_name)}'.strip('.')
        email = f'{base_email}@knovistatechnologies.com'
        suffix = 2
        while User.objects.using(database).filter(email__iexact=email).exists() or Employee.objects.using(database).filter(email__iexact=email).exists():
            email = f'{base_email}{suffix}@knovistatechnologies.com'
            suffix += 1

        user = User.objects.using(database).create(
            username=email,
            email=email,
            password=make_password(None),
            first_name=first_name,
            last_name=last_name,
            is_active=True,
        )
        UserProfile.objects.using(database).create(user_id=user.pk, role='employee')
        Employee.objects.using(database).create(
            user_id=user.pk,
            first_name=first_name,
            last_name=last_name,
            emp_name=full_name,
            email=email,
            department_id=department.pk,
            joining_date=joining_date,
            designation=designation,
            location='Operational Office',
            current_salary=0,
            status='active',
        )


class Migration(migrations.Migration):
    dependencies = [
        ('Accounts', '0001_initial'),
        ('Ems_app', '0002_paysliprequest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(seed_team_roster, migrations.RunPython.noop),
    ]
