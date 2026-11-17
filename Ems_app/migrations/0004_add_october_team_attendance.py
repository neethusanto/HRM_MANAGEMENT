"""Add the October 2026 attendance requested for the seeded team roster."""
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import migrations
from django.utils import timezone


ROSTER_NAMES = [
    'Sreehari R', 'Nithin Prathapan', 'Jesbin Thomas', 'Parvathy Raju',
    'Deepthy E P', 'Chinchina Lalu', 'Akhila Subash V', 'Namitha C',
    'Radhika KJ', 'Riya Roy', 'Sreerag M S', 'Sreeraj T',
    'Gautham K Sasidharan', 'Anandhu', 'Nandhu', 'Srrechanthu', 'Rifan',
    'Joshy', 'Akhil', 'Riswana', 'Megha', 'Siju', 'Janasuthii', 'Basith',
    'Adithyan', 'Gopika', 'Jai On',
]


def add_team_attendance(apps, schema_editor):
    Employee = apps.get_model('Ems_app', 'Employee')
    Attendance = apps.get_model('Ems_app', 'Attendance')
    database = schema_editor.connection.alias
    start = date(2026, 10, 1)
    end = date(2026, 10, 31)
    local_tz = timezone.get_current_timezone()

    # Follow the same October schedule already entered for Neethu: 09:30 office
    # start, regular 9-hour days, and the 3-hour half-day on October 31.
    template_employee = Employee.objects.using(database).filter(emp_name__istartswith='Neethu').first()
    template_records = {}
    if template_employee:
        template_records = {
            record.date: record
            for record in Attendance.objects.using(database).filter(
                employee_id=template_employee.pk,
                date__gte=start,
                date__lte=end,
            )
        }

    current = start
    while current <= end:
        # October 2 (Gandhi Jayanti), Sundays, and the second Saturday are off.
        is_second_saturday = current.weekday() == 5 and 8 <= current.day <= 14
        if current.day == 2 or current.weekday() == 6 or is_second_saturday:
            current += timedelta(days=1)
            continue

        template = template_records.get(current)
        if template and template.check_in:
            check_in_local = timezone.localtime(template.check_in, local_tz)
            check_in_time = check_in_local.timetz().replace(tzinfo=None)
        else:
            check_in_time = time(9, 30)
        if template and template.check_out:
            check_out_local = timezone.localtime(template.check_out, local_tz)
            check_out_time = check_out_local.timetz().replace(tzinfo=None)
        else:
            check_out_time = time(12, 30) if current.day == 31 else time(18, 30)

        check_in = timezone.make_aware(datetime.combine(current, check_in_time), local_tz)
        check_out = timezone.make_aware(datetime.combine(current, check_out_time), local_tz)
        status = template.status if template else ('half_day' if current.day == 31 else 'present')
        work_mode = template.work_mode if template else 'office'

        for full_name in ROSTER_NAMES:
            employee = Employee.objects.using(database).filter(emp_name__iexact=full_name).first()
            if not employee:
                continue
            if employee.joining_date != start:
                employee.joining_date = start
                employee.save(using=database, update_fields=['joining_date'])
            if not Attendance.objects.using(database).filter(employee_id=employee.pk, date=current).exists():
                Attendance.objects.using(database).create(
                    employee_id=employee.pk,
                    date=current,
                    check_in=check_in,
                    check_out=check_out,
                    status=status,
                    work_mode=work_mode,
                    # HR must verify these manually entered records before payroll uses them.
                    is_verified=False,
                    verified_by_id=None,
                    verified_at=None,
                )
        current += timedelta(days=1)


class Migration(migrations.Migration):
    dependencies = [
        ('Ems_app', '0003_seed_team_roster'),
    ]

    operations = [
        migrations.RunPython(add_team_attendance, migrations.RunPython.noop),
    ]
