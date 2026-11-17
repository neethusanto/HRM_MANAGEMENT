import csv
import io
import calendar
import hmac
import json
import math
import secrets
import string
from datetime import datetime, date, timedelta
from io import BytesIO

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.db import models
from django.db.models import Count, Sum
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# Excel
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)

from Accounts.decorators import (
    admin_required, hr_required, employee_required, admin_or_hr_required
)
from .models import Department, Employee, Attendance, Leave, Salary, PayslipRequest
from .forms import DepartmentForm, EmployeeForm, ManualAttendanceForm, LeaveForm, SalaryForm, HRForm


def _parse_datetime(value, base_date):
    """
    Parse a datetime / time / string into an aware datetime.
    base_date is used when value carries only a time (e.g. "09:15").
    """
    if value is None or value == '':
        return None
    # Already a full datetime
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    # Excel time object (data_only=True returns datetime.time)
    if hasattr(value, 'hour') and hasattr(value, 'minute') and not isinstance(value, date):
        dt = datetime.combine(base_date, value)
        return timezone.make_aware(dt)
    # String fallback
    s = str(value).strip()
    for fmt in (
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S',
        '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M',
        '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M',
        '%H:%M:%S', '%H:%M',
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt in ('%H:%M:%S', '%H:%M'):
                dt = datetime.combine(base_date, dt.time())
            return timezone.make_aware(dt)
        except ValueError:
            continue
    return None


# ============================================================
# HELPER — Working days: Mon-Sat, except Sundays + 2nd Saturday
# ============================================================
def _get_month_working_days(year, month):
    """
    Return number of working days in the month.
    Rules:
      • Monday → Saturday = working days
      • All Sundays        = holiday
      • 2nd Saturday       = holiday
    """
    _, num_days = calendar.monthrange(year, month)

    # ---- Count working days ----
    count = 0
    for day in range(1, num_days + 1):
        d = date(year, month, day)
        if _attendance_holiday_name(d):
            continue
        count += 1
    return count


def _attendance_holiday_name(day):
    if day.month == 10 and day.day == 2:
        return 'Gandhi Jayanti'
    if day.weekday() == 6:
        return 'Sunday'
    if day.weekday() == 5 and 8 <= day.day <= 14:
        return 'Second Saturday'
    return ''


def _verified_attendance_pay(salary):
    """Return attendance-based gross pay using only complete HR-verified days."""
    working_days = _get_month_working_days(
        salary.salary_month.year, salary.salary_month.month,
    )
    cached_attendances = getattr(salary.employee, '_prefetched_objects_cache', {}).get('attendances')
    if cached_attendances is not None:
        records = [
            record for record in cached_attendances
            if record.date.year == salary.salary_month.year
            and record.date.month == salary.salary_month.month
            and record.is_verified and record.check_in and record.check_out
        ]
        full_days = sum(record.status in ('present', 'late') for record in records)
        half_days = sum(record.status == 'half_day' for record in records)
    else:
        records = Attendance.objects.filter(
            employee=salary.employee,
            date__year=salary.salary_month.year,
            date__month=salary.salary_month.month,
            is_verified=True,
            check_in__isnull=False,
            check_out__isnull=False,
        )
        full_days = records.filter(status__in=['present', 'late']).count()
        half_days = records.filter(status='half_day').count()
    paid_days = min(full_days + half_days * 0.5, working_days)
    per_day_rate = round(float(salary.basic_salary) / working_days, 2) if working_days else 0
    attendance_pay = round(per_day_rate * paid_days, 2)
    return attendance_pay, round(attendance_pay + float(salary.bonus) - float(salary.deductions), 2)


def _parse_date(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'date'):
        return value.date()
    s = str(value).strip()
    for fmt in (
        '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y',
        '%d-%b-%Y', '%d-%B-%Y', '%d %b %Y', '%d %B %Y',
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _generate_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# ============================================================
# LEAVE POLICY
# ============================================================
CASUAL_LEAVE_LIMIT_PER_MONTH = 2

# Office timing
OFFICE_START     = datetime.strptime('09:30', '%H:%M').time()
OFFICE_GRACE_END = datetime.strptime('09:40', '%H:%M').time()


# ============================================================
# ADMIN DASHBOARD
# ============================================================
@login_required
@admin_required
def admin_dashboard(request):
    total_employees = Employee.objects.count()
    total_hr = Employee.objects.filter(is_hr=True).count()
    total_departments = Department.objects.count()
    today = timezone.localdate()
    today_attendance = Attendance.objects.filter(date=today)
    present_count = today_attendance.filter(status='present').count()
    absent_count = today_attendance.filter(status='absent').count()
    late_count = today_attendance.filter(status='late').count()
    half_day_count = today_attendance.filter(status='half_day').count()
    pending_leaves = Leave.objects.filter(status='pending').count()
    recent_employees = Employee.objects.order_by('-created_at')[:5]
    recent_leaves = Leave.objects.filter(status='pending').order_by('-applied_on')[:5]
    dept_wise = Department.objects.annotate(emp_count=Count('employee'))
    self_employee = _linked_attendance_employee(request)
    self_attendance = Attendance.objects.filter(employee=self_employee, date=today).first() if self_employee else None
    self_leave = Leave.objects.filter(
        employee=self_employee, status='approved', start_date__lte=today, end_date__gte=today,
    ).first() if self_employee else None
    context = {
        'total_employees': total_employees,
        'total_hr': total_hr,
        'total_departments': total_departments,
        'present_count': present_count,
        'absent_count': absent_count,
        'late_count': late_count,
        'half_day_count': half_day_count,
        'pending_leaves': pending_leaves,
        'recent_employees': recent_employees,
        'recent_leaves': recent_leaves,
        'dept_wise': dept_wise,
        'employee': self_employee,
        'clock_attendance': self_attendance,
        'on_leave': self_leave,
        'today': today,
        'office_location_configured': bool(
            getattr(settings, 'COMPANY_LATITUDE', None)
            and getattr(settings, 'COMPANY_LONGITUDE', None)
        ),
    }
    return render(request, 'admin/admin_dashboard.html', context)


# ============================================================
# DEPARTMENT
# ============================================================
@login_required
@admin_required
def department_list(request):
    departments = Department.objects.all().order_by('dept_name')
    name_q = (request.GET.get('name') or '').strip()
    if name_q:
        departments = departments.filter(dept_name__icontains=name_q)
    return render(request, 'admin/department_list.html', {
        'departments': departments,
        'name_q': name_q,
    })


@login_required
@admin_required
def department_create(request):
    if request.method == 'POST':
        form = DepartmentForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Department created successfully!')
            return redirect('Ems_app:department_list')
    else:
        form = DepartmentForm()
    return render(request, 'admin/department_form.html',
                  {'form': form, 'title': 'Create Department'})


@login_required
@admin_required
def department_edit(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        form = DepartmentForm(request.POST, instance=department)
        if form.is_valid():
            form.save()
            messages.success(request, 'Department updated successfully!')
            return redirect('Ems_app:department_list')
    else:
        form = DepartmentForm(instance=department)
    return render(request, 'admin/department_form.html',
                  {'form': form, 'title': 'Edit Department'})


@login_required
@admin_required
def department_delete(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        department.delete()
        messages.success(request, 'Department deleted successfully!')
        return redirect('Ems_app:department_list')
    return render(request, 'admin/department_confirm_delete.html',
                  {'department': department})


# ============================================================
# EMPLOYEE MANAGEMENT
# ============================================================
@login_required
@admin_or_hr_required
def employee_list(request):
    employees = Employee.objects.all().select_related('department', 'user')
    departments = Department.objects.all().order_by('dept_name')
    dept_id = request.GET.get('department') or ''
    name_q = (request.GET.get('name') or '').strip()
    if dept_id:
        employees = employees.filter(department_id=dept_id)
    if name_q:
        employees = employees.filter(
            models.Q(emp_name__icontains=name_q) |
            models.Q(first_name__icontains=name_q) |
            models.Q(last_name__icontains=name_q) |
            models.Q(email__icontains=name_q)
        )
    employees = list(employees.order_by('emp_name'))
    today = timezone.localdate()
    attendance_by_employee = {
        attendance.employee_id: attendance
        for attendance in Attendance.objects.filter(employee_id__in=[e.pk for e in employees], date=today)
    }
    for employee in employees:
        employee.today_attendance = attendance_by_employee.get(employee.pk)
    return render(request, 'admin/employee_list.html', {
        'employees': employees,
        'today': today,
        'employee_count': len(employees),
        'departments': departments,
        'selected_dept': dept_id,
        'name_q': name_q,
    })


@login_required
def team_calendar(request):
    """Show the organization calendar to HR/admin and a department team to employees."""
    today = timezone.localdate()
    role = getattr(getattr(request.user, 'profile', None), 'role', None)
    if role not in ('admin', 'hr', 'employee'):
        return redirect('Accounts:dashboard_redirect')
    employees_query = Employee.objects.filter(status='active').select_related('department')
    team_department = None
    if role == 'employee':
        try:
            current_employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, 'No employee profile is linked to your account.')
            return redirect('Accounts:dashboard_redirect')
        team_department = current_employee.department
        if team_department:
            employees_query = employees_query.filter(department=team_department)
        else:
            employees_query = employees_query.filter(pk=current_employee.pk)
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
        if year < 2000 or year > 2100 or not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    previous_month = month_start - timedelta(days=1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    employees = list(employees_query.order_by('emp_name'))
    employee_ids = [employee.pk for employee in employees]
    attendance_by_key = {
        (item.employee_id, item.date): item
        for item in Attendance.objects.filter(
            employee_id__in=employee_ids, date__gte=month_start, date__lte=month_end,
        )
    }
    approved_leaves = Leave.objects.filter(
        employee_id__in=employee_ids,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    ).select_related('employee')
    leave_by_key = {}
    for leave in approved_leaves:
        day = max(leave.start_date, month_start)
        end = min(leave.end_date, month_end)
        while day <= end:
            leave_by_key.setdefault((leave.employee_id, day), leave)
            day += timedelta(days=1)

    month_days = []
    _, month_length = calendar.monthrange(year, month)
    for day_number in range(1, month_length + 1):
        day = date(year, month, day_number)
        month_days.append({
            'date': day,
            'weekday': day.strftime('%a'),
            'is_weekend': day.weekday() >= 5,
        })

    today_attendance = {
        item.employee_id: item
        for item in Attendance.objects.filter(employee_id__in=employee_ids, date=today)
    }
    today_leave_ids = set(Leave.objects.filter(
        employee_id__in=employee_ids,
        status='approved',
        start_date__lte=today,
        end_date__gte=today,
    ).values_list('employee_id', flat=True))
    on_leave_today = [employee for employee in employees if employee.pk in today_leave_ids]
    not_in_yet = [
        employee for employee in employees
        if employee.pk not in today_leave_ids
        and not (today_attendance.get(employee.pk) and today_attendance[employee.pk].check_in)
    ]

    on_time_count = late_count = home_count = pending_count = 0
    for employee in employees:
        employee.today_attendance = today_attendance.get(employee.pk)
        attendance = employee.today_attendance
        if attendance:
            if attendance.status == 'present':
                on_time_count += 1
            elif attendance.status == 'late':
                late_count += 1
            if attendance.work_mode == 'home' and attendance.check_in:
                home_count += 1
            if attendance.check_in and attendance.check_out and not attendance.is_verified:
                pending_count += 1

        employee.calendar_days = []
        for day_info in month_days:
            day = day_info['date']
            record = attendance_by_key.get((employee.pk, day))
            leave = leave_by_key.get((employee.pk, day))
            holiday_name = _attendance_holiday_name(day)
            kind, label, short_label = '', '', ''
            if day < employee.joining_date:
                kind, label, short_label = 'not-employed', 'Before start date', ''
            elif record:
                if record.work_mode == 'home':
                    kind, label, short_label = 'wfh', 'Work from home', 'WFH'
                elif record.status == 'present':
                    kind, label, short_label = 'present', 'Present', 'P'
                elif record.status == 'late':
                    kind, label, short_label = 'late', 'Late arrival', 'L'
                elif record.status == 'half_day':
                    kind, label, short_label = 'half-day', 'Half day', '½'
                elif record.status == 'leave':
                    kind, label, short_label = 'paid-leave', 'Leave', 'LV'
                elif record.status == 'absent':
                    kind, label, short_label = 'absent', 'Absent', 'A'
                if record.check_in and record.check_out:
                    label = f'{label} · {record.working_hours}'
            elif leave and not holiday_name:
                if leave.leave_type == 'unpaid':
                    kind, label, short_label = 'unpaid-leave', 'Unpaid leave', 'UL'
                else:
                    kind, label, short_label = 'paid-leave', 'Approved leave', 'PL'
            elif holiday_name:
                if holiday_name in ('Sunday', 'Second Saturday'):
                    kind, label, short_label = 'weekly-off', holiday_name, 'WO'
                else:
                    kind, label, short_label = 'holiday', holiday_name, 'H'
            elif day <= today:
                kind, label, short_label = 'no-attendance', 'No attendance record', '—'

            employee.calendar_days.append({
                'date': day,
                'kind': kind,
                'label': label,
                'short_label': short_label,
                'pending_review': bool(record and record.check_in and record.check_out and not record.is_verified),
                'is_today': day == today,
            })

    return render(request, 'hr/team_calendar.html', {
        'employees': employees,
        'employee_count': len(employees),
        'user_role': role,
        'team_department': team_department,
        'month_days': month_days,
        'month_label': month_start.strftime('%B %Y'),
        'selected_month': month,
        'selected_year': year,
        'previous_month': previous_month,
        'next_month': next_month,
        'today': today,
        'on_leave_today': on_leave_today,
        'on_leave_ids': today_leave_ids,
        'not_in_yet': not_in_yet,
        'on_time_count': on_time_count,
        'late_count': late_count,
        'home_count': home_count,
        'pending_count': pending_count,
    })


@login_required
@admin_or_hr_required
def employee_create(request):
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES)
        if form.is_valid():
            email = form.cleaned_data['email'].strip().lower()
            password = form.cleaned_data['password']
            phone = form.cleaned_data.get('phone') or None
            if User.objects.filter(email__iexact=email).exists() or \
               User.objects.filter(username__iexact=email).exists():
                messages.error(request, f'A user with email "{email}" already exists.')
                return render(request, 'admin/employee_form.html',
                              {'form': form, 'title': 'Add Employee'})
            user = User.objects.create_user(
                username=email, email=email, password=password,
                first_name=form.cleaned_data.get('first_name', ''),
                last_name=form.cleaned_data.get('last_name', ''),
            )
            user.profile.role = 'employee'
            user.profile.phone = phone or ''
            user.profile.save()
            employee = form.save(commit=False)
            employee.user = user
            employee.email = email
            employee.phone = phone
            employee.save()
            messages.success(request, f'Employee created! Login with email "{email}".')
            return redirect('Ems_app:employee_list')
    else:
        form = EmployeeForm()
    return render(request, 'admin/employee_form.html',
                  {'form': form, 'title': 'Add Employee'})


@login_required
@admin_or_hr_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        form = EmployeeForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            new_password = form.cleaned_data.get('password')
            new_email = form.cleaned_data['email'].strip().lower()
            phone = form.cleaned_data.get('phone') or None
            employee = form.save(commit=False)
            employee.email = new_email
            employee.phone = phone
            user = employee.user
            if new_email and new_email != user.username:
                if User.objects.filter(username__iexact=new_email).exclude(pk=user.pk).exists() or \
                   User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
                    messages.error(request, f'Email "{new_email}" is already taken.')
                    return render(request, 'admin/employee_form.html',
                                  {'form': form, 'title': 'Edit Employee'})
                user.username = new_email
                user.email = new_email
            if new_password:
                user.set_password(new_password)
            user.save()
            employee.save()
            messages.success(request, 'Employee updated successfully!')
            return redirect('Ems_app:employee_list')
    else:
        form = EmployeeForm(instance=employee)
    return render(request, 'admin/employee_form.html',
                  {'form': form, 'title': 'Edit Employee'})


@login_required
@admin_or_hr_required
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        user = employee.user
        employee.delete()
        if user:
            user.delete()
        messages.success(request, 'Employee deleted successfully!')
        return redirect('Ems_app:employee_list')
    return render(request, 'admin/employee_confirm_delete.html',
                  {'employee': employee})


# ============================================================
# EMPLOYEE BULK UPLOAD (EXCEL)
# ============================================================
@login_required
@admin_or_hr_required
def employee_bulk_upload(request):
    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        if not excel_file:
            messages.error(request, 'Please select an Excel file (.xlsx).')
            return render(request, 'admin/employee_bulk_upload.html')
        if not excel_file.name.lower().endswith('.xlsx'):
            messages.error(request, 'Only .xlsx files are supported.')
            return render(request, 'admin/employee_bulk_upload.html')
        try:
            wb = load_workbook(excel_file, data_only=True)
        except Exception as e:
            messages.error(request, f'Could not read the file: {e}')
            return render(request, 'admin/employee_bulk_upload.html')
        ws = wb.active
        header = [(cell.value or '').strip().lower() for cell in ws[1]]
        required_cols = ['first_name', 'last_name', 'email', 'joining_date']
        missing = [c for c in required_cols if c not in header]
        if missing:
            messages.error(request, f'Missing required columns: {", ".join(missing)}.')
            return render(request, 'admin/employee_bulk_upload.html')
        col = {name: idx for idx, name in enumerate(header)}
        created = []
        failed = []
        default_password = 'Employee@2026'
        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            def val(name):
                idx = col.get(name)
                if idx is None or idx >= len(row):
                    return None
                v = row[idx]
                return v.strip() if isinstance(v, str) else v
            email = (val('email') or '')
            if not email:
                failed.append((row_num, 'Missing email'))
                continue
            email = str(email).lower()
            first_name = val('first_name') or ''
            last_name = val('last_name') or ''
            if not first_name and not last_name:
                failed.append((row_num, 'Missing name'))
                continue
            if User.objects.filter(email__iexact=email).exists() or \
               User.objects.filter(username__iexact=email).exists():
                failed.append((row_num, f'Email "{email}" already exists'))
                continue
            emp_code = val('employee_code')
            if emp_code:
                emp_code = str(emp_code).strip().upper()
                if Employee.objects.filter(employee_code__iexact=emp_code).exists():
                    failed.append((row_num, f'Employee code "{emp_code}" already exists'))
                    continue
            dept_name = val('department')
            dept = None
            if dept_name:
                dept = Department.objects.filter(dept_name__iexact=str(dept_name).strip()).first()
                if not dept:
                    failed.append((row_num, f'Department "{dept_name}" not found'))
                    continue
            joining = _parse_date(val('joining_date'))
            if not joining:
                failed.append((row_num, 'Invalid joining_date'))
                continue
            dob = _parse_date(val('date_of_birth'))
            phone = val('phone')
            if phone:
                phone = str(phone).strip()
                if Employee.objects.filter(phone=phone).exists():
                    failed.append((row_num, f'Phone "{phone}" already exists'))
                    continue
            else:
                phone = None
            try:
                current_salary = val('current_salary') or 0
                current_salary = float(current_salary) if current_salary else 0
            except (ValueError, TypeError):
                current_salary = 0
            try:
                user = User.objects.create_user(
                    username=email, email=email,
                    password=default_password,
                    first_name=first_name, last_name=last_name,
                )
                user.profile.role = 'employee'
                user.profile.phone = phone or ''
                user.profile.save()
                Employee.objects.create(
                    user=user,
                    employee_code=emp_code or None,
                    first_name=first_name, last_name=last_name,
                    emp_name=f'{first_name} {last_name}'.strip(),
                    email=email, phone=phone,
                    date_of_birth=dob,
                    department=dept,
                    joining_date=joining,
                    designation=val('designation') or '',
                    location=val('location') or 'Operational Office',
                    experience=str(val('experience') or ''),
                    current_salary=current_salary,
                )
                created.append(f'{first_name} {last_name} ({email})')
            except Exception as e:
                failed.append((row_num, str(e)[:120]))
                try:
                    User.objects.filter(email__iexact=email).delete()
                except Exception:
                    pass
        if created:
            messages.success(request, f'{len(created)} employee(s) created. Default password: "{default_password}"')
        if failed:
            preview = '; '.join(f'Row {r}: {msg}' for r, msg in failed[:5])
            more = f' (+{len(failed) - 5} more)' if len(failed) > 5 else ''
            messages.warning(request, f'{len(failed)} row(s) failed. {preview}{more}')
        return redirect('Ems_app:employee_list')
    return render(request, 'admin/employee_bulk_upload.html')


# ============================================================
# HR MANAGEMENT
# ============================================================
@login_required
@admin_required
def hr_list(request):
    hr_users = Employee.objects.filter(is_hr=True)
    return render(request, 'admin/hr_list.html', {'hr_users': hr_users})


@login_required
@admin_required
def hr_create(request):
    if request.method == 'POST':
        form = HRForm(request.POST, request.FILES)
        if form.is_valid():
            email = form.cleaned_data['email'].strip().lower()
            if User.objects.filter(email__iexact=email).exists() or \
               User.objects.filter(username__iexact=email).exists():
                messages.error(request, f'A user with email "{email}" already exists.')
                return render(request, 'admin/hr_form.html',
                              {'form': form, 'title': 'Add HR User'})
            user = User.objects.create_user(
                username=email, email=email,
                password=form.cleaned_data['password'],
                first_name=form.cleaned_data.get('first_name', ''),
                last_name=form.cleaned_data.get('last_name', ''),
            )
            user.profile.role = 'hr'
            user.profile.phone = form.cleaned_data.get('phone', '') or ''
            user.profile.save()
            employee = form.save(commit=False)
            employee.user = user
            employee.email = email
            employee.is_hr = True
            employee.save()
            messages.success(request, f'HR user created! Login with email "{email}".')
            return redirect('Ems_app:hr_list')
    else:
        form = HRForm()
    return render(request, 'admin/hr_form.html',
                  {'form': form, 'title': 'Add HR User'})


@login_required
@admin_required
def hr_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk, is_hr=True)
    if request.method == 'POST':
        form = HRForm(request.POST, request.FILES, instance=employee)
        if form.is_valid():
            new_password = form.cleaned_data.get('password')
            new_email = form.cleaned_data['email'].strip().lower()
            employee = form.save(commit=False)
            employee.email = new_email
            user = employee.user
            if new_email != user.username:
                if User.objects.filter(username__iexact=new_email).exclude(pk=user.pk).exists():
                    messages.error(request, f'Email "{new_email}" is already taken.')
                    return render(request, 'admin/hr_form.html', {'form': form, 'title': 'Edit HR User'})
                user.username = new_email
                user.email = new_email
            if new_password:
                user.set_password(new_password)
            user.save()
            employee.save()
            messages.success(request, 'HR user updated successfully!')
            return redirect('Ems_app:hr_list')
    else:
        form = HRForm(instance=employee)
    return render(request, 'admin/hr_form.html', {'form': form, 'title': 'Edit HR User'})


@login_required
@admin_required
def hr_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        user = employee.user
        employee.delete()
        if user:
            user.delete()
        messages.success(request, 'HR user deleted successfully!')
        return redirect('Ems_app:hr_list')
    return render(request, 'admin/hr_confirm_delete.html', {'employee': employee})


# ============================================================
# ATTENDANCE
# ============================================================
@login_required
@admin_or_hr_required
def attendance_list(request):
    """
    Attendance list with 4 filter modes:
      1. Specific date  → (?date=2026-10-05)
      2. Date range     → (?from_date=2026-10-01&to_date=2026-10-15)
      3. Month + Year   → (?month=10&year=2026)
      4. Default        → today
    Plus employee / department filters.
    Priority: specific date > range > month+year > today
    """
    employees = Employee.objects.all().order_by('emp_name')
    departments = Department.objects.all().order_by('dept_name')

    # ---------- Read filter params ----------
    dept_id   = request.GET.get('department') or ''
    emp_id    = request.GET.get('employee') or ''
    date_str  = request.GET.get('date') or ''
    from_date = request.GET.get('from_date') or ''
    to_date   = request.GET.get('to_date') or ''
    month     = request.GET.get('month') or ''
    year      = request.GET.get('year') or ''

    qs = Attendance.objects.select_related('employee', 'employee__department')

    # ---------- Employee / Department filter ----------
    if dept_id:
        qs = qs.filter(employee__department_id=dept_id)
    if emp_id:
        qs = qs.filter(employee_id=emp_id)

    # ============================================================
    # DATE FILTER (priority order)
    # ============================================================
    filter_mode = 'today'
    if date_str:
        # 1️⃣ Specific date
        filter_mode = 'date'
        qs = qs.filter(date=date_str)
    elif from_date or to_date:
        # 2️⃣ Date range
        filter_mode = 'range'
        if from_date: qs = qs.filter(date__gte=from_date)
        if to_date:   qs = qs.filter(date__lte=to_date)
        date_str = ''
    elif month:
        # 3️⃣ Month [+ optional year override]
        filter_mode = 'month'
        try:
            year_int = int(year) if year else timezone.now().year
        except (ValueError, TypeError):
            year_int = timezone.now().year
        qs = qs.filter(date__month=int(month), date__year=year_int)
        date_str = ''
    else:
        # 4️⃣ Default — today
        today = timezone.localdate()
        qs = qs.filter(date=today)
        date_str = ''

    qs = qs.order_by('-date', 'employee__emp_name')

    # ---------- Summary for the filtered set ----------
    summary = {
        'present':  qs.filter(status='present').count(),
        'absent':   qs.filter(status='absent').count(),
        'late':     qs.filter(status='late').count(),
        'half_day': qs.filter(status='half_day').count(),
        'leave':    qs.filter(status='leave').count(),
        'total':    qs.count(),
    }

    # ---------- Dropdown data ----------
    current_year = timezone.now().year
    years = list(range(current_year - 3, current_year + 2))
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    return render(request, 'hr/attendance_list.html', {
        'attendances': qs,
        'employees': employees,
        'departments': departments,
        # Selected filter values (to repopulate the form)
        'selected_dept': dept_id,
        'selected_emp': emp_id,
        'date_filter': date_str,
        'from_date': from_date,
        'to_date': to_date,
        'selected_month': month,
        'selected_year': year,
        # Dropdown choices
        'months': months,
        'years': years,
        # Summary + mode
        'summary': summary,
        'filter_mode': filter_mode,
    })


@login_required
@admin_or_hr_required
def attendance_create(request):
    if request.method == 'POST':
        form = ManualAttendanceForm(request.POST)
        if form.is_valid():
            attendance = form.save(commit=False)
            attendance.is_verified = False
            attendance.verified_by = None
            attendance.verified_at = None
            attendance.save()
            messages.success(request, 'Attendance entry created. It is awaiting HR verification.')
            return redirect('Ems_app:attendance_list')
    else:
        form = ManualAttendanceForm()
    return render(request, 'hr/attendance_form.html', {'form': form})


def _linked_attendance_employee(request):
    role = getattr(getattr(request.user, 'profile', None), 'role', None)
    if role not in ('admin', 'hr', 'employee'):
        return None
    try:
        return request.user.employee
    except Employee.DoesNotExist:
        return None


@login_required
def employee_attendance(request):
    employee = _linked_attendance_employee(request)
    if not employee:
        messages.error(request, 'No employee attendance profile is linked to your account.')
        return redirect('Accounts:dashboard_redirect')
    attendances = Attendance.objects.filter(employee=employee).order_by('-date')
    today = timezone.localdate()
    today_attendance = attendances.filter(date=today).first()
    on_leave = Leave.objects.filter(
        employee=employee, status='approved',
        start_date__lte=today, end_date__gte=today,
    ).first()
    return render(request, 'employee/attendance.html', {
        'attendance': today_attendance,
        'attendances': attendances,
        'today': today,
        'on_leave': on_leave,
        'office_location_configured': bool(
            getattr(settings, 'COMPANY_LATITUDE', None)
            and getattr(settings, 'COMPANY_LONGITUDE', None)
        ),
    })


@login_required
def employee_attendance_calendar(request, pk=None):
    role = getattr(getattr(request.user, 'profile', None), 'role', None)
    if pk is not None and role in ('admin', 'hr'):
        employee = get_object_or_404(Employee, pk=pk)
    else:
        employee = _linked_attendance_employee(request)
        if pk is not None and (not employee or employee.pk != pk):
            messages.error(request, 'You may only view your own attendance calendar.')
            return redirect('Accounts:dashboard_redirect')
    if not employee:
        messages.error(request, 'No employee attendance profile is linked to your account.')
        return redirect('Accounts:dashboard_redirect')
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
        if year < 2000 or year > 2100 or month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month
    attendance_by_date = {
        item.date: item for item in Attendance.objects.filter(
            employee=employee, date__year=year, date__month=month,
        )
    }
    weeks = []
    for dates in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        week = []
        for day in dates:
            week.append({
                'date': day,
                'in_month': day.month == month,
                'attendance': attendance_by_date.get(day),
                'holiday_name': _attendance_holiday_name(day),
            })
        weeks.append(week)
    previous = date(year, month, 1) - timedelta(days=1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return render(request, 'employee/attendance_calendar.html', {
        'employee': employee,
        'weeks': weeks,
        'selected_month': month,
        'selected_year': year,
        'calendar_for_other': pk is not None and role in ('admin', 'hr'),
        'month_label': date(year, month, 1).strftime('%B %Y'),
        'previous_month': previous,
        'next_month': next_month,
        'today': today,
    })


@login_required
def check_in(request):
    if request.method != 'POST':
        return redirect('Ems_app:employee_attendance')
    employee = _linked_attendance_employee(request)
    if not employee:
        messages.error(request, 'No employee attendance profile is linked to your account.')
        return redirect('Accounts:dashboard_redirect')
    if employee.status == 'inactive':
        messages.error(request, 'Your account is inactive. Contact admin.')
        return redirect('Ems_app:employee_attendance')
    work_mode = request.POST.get('work_mode', 'office')
    if work_mode not in ('office', 'home'):
        messages.error(request, 'Choose office or work from home.')
        return redirect('Ems_app:employee_attendance')

    if work_mode == 'office':
        company_lat = getattr(settings, 'COMPANY_LATITUDE', None)
        company_lon = getattr(settings, 'COMPANY_LONGITUDE', None)
        try:
            company_lat = float(company_lat)
            company_lon = float(company_lon)
            employee_lat = float(request.POST.get('latitude', ''))
            employee_lon = float(request.POST.get('longitude', ''))
            if not all(math.isfinite(value) for value in (company_lat, company_lon, employee_lat, employee_lon)):
                raise ValueError
            if not (-90 <= company_lat <= 90 and -90 <= employee_lat <= 90
                    and -180 <= company_lon <= 180 and -180 <= employee_lon <= 180):
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, 'Office location is not configured or your location could not be read. Contact HR or choose Work from home.')
            return redirect('Ems_app:employee_attendance')
        radius = max(1, int(getattr(settings, 'COMPANY_GEOFENCE_RADIUS_METERS', 150)))
        lat1, lat2 = math.radians(company_lat), math.radians(employee_lat)
        dlat = lat2 - lat1
        dlon = math.radians(employee_lon - company_lon)
        haversine = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        distance = 6_371_000 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
        if distance > radius:
            messages.error(request, f'Office check-in is allowed within {radius} meters of the company location.')
            return redirect('Ems_app:employee_attendance')

    today = timezone.localdate()
    holiday_name = _attendance_holiday_name(today)
    if holiday_name:
        messages.error(request, f'Today is {holiday_name}; check-in is unavailable.')
        return redirect('Ems_app:employee_attendance')
    if Leave.objects.filter(
        employee=employee, status='approved', start_date__lte=today, end_date__gte=today,
    ).exists():
        messages.error(request, 'You are on approved leave today and cannot check in.')
        return redirect('Ems_app:employee_attendance')
    att, _ = Attendance.objects.get_or_create(
        employee=employee, date=today,
        defaults={'status': 'present'},
    )
    if att.check_in:
        messages.warning(request, 'You have already checked in today.')
        return redirect('Ems_app:employee_attendance')
    if att.status == 'leave':
        messages.error(request, 'You are marked on leave today. Contact HR if this is incorrect.')
        return redirect('Ems_app:employee_attendance')
    now = timezone.localtime()
    att.check_in = now
    att.work_mode = work_mode
    att.is_verified = False
    att.verified_by = None
    att.verified_at = None
    if now.time() <= OFFICE_GRACE_END:
        att.status = 'present'
    else:
        att.status = 'late'
    att.save()
    messages.success(request, f'Checked in at {now.strftime("%H:%M")}.')
    return redirect('Ems_app:employee_attendance')


@login_required
def check_out(request):
    if request.method != 'POST':
        return redirect('Ems_app:employee_attendance')
    employee = _linked_attendance_employee(request)
    if not employee:
        messages.error(request, 'No employee attendance profile is linked to your account.')
        return redirect('Accounts:dashboard_redirect')
    today = timezone.localdate()
    att = Attendance.objects.filter(employee=employee, date=today).first()
    if not att or not att.check_in:
        messages.error(request, 'Please check in first.')
        return redirect('Ems_app:employee_attendance')
    if att.check_out:
        messages.warning(request, 'You have already checked out today.')
        return redirect('Ems_app:employee_attendance')
    now = timezone.localtime()
    att.check_out = now
    if (now - att.check_in).total_seconds() < 4 * 60 * 60:
        att.status = 'half_day'
    att.is_verified = False
    att.verified_by = None
    att.verified_at = None
    att.save()
    messages.success(request, f'Checked out at {now.strftime("%H:%M")}. Total: {att.working_hours}.')
    return redirect('Ems_app:employee_attendance')


@login_required
@admin_or_hr_required
def attendance_verify(request, pk):
    if request.method != 'POST':
        return redirect('Ems_app:attendance_list')
    attendance = get_object_or_404(Attendance, pk=pk)
    if not attendance.check_in or not attendance.check_out:
        messages.error(request, 'Attendance can be verified after check-out is recorded.')
    else:
        attendance.is_verified = True
        attendance.verified_by = request.user
        attendance.verified_at = timezone.now()
        attendance.save(update_fields=['is_verified', 'verified_by', 'verified_at', 'updated_at'])
        messages.success(request, f'Attendance for {attendance.employee.emp_name} verified.')
    return redirect(request.META.get('HTTP_REFERER') or 'Ems_app:attendance_list')


@login_required
@admin_or_hr_required
def attendance_edit(request, pk):
    att = get_object_or_404(Attendance, pk=pk)
    if request.method == 'POST':
        check_in_str = request.POST.get('check_in') or ''
        check_out_str = request.POST.get('check_out') or ''
        status = request.POST.get('status', att.status)

        def parse_dt(s):
            if not s:
                return None
            try:
                dt = datetime.strptime(s, '%Y-%m-%dT%H:%M')
                return timezone.make_aware(dt)
            except ValueError:
                return None

        att.check_in = parse_dt(check_in_str)
        att.check_out = parse_dt(check_out_str)
        att.status = status
        att.is_verified = False
        att.verified_by = None
        att.verified_at = None
        att.save()
        messages.success(request, 'Attendance corrected successfully!')
        return redirect('Ems_app:attendance_list')
    return render(request, 'hr/attendance_edit.html', {'attendance': att})


@login_required
@admin_required
def attendance_delete(request, pk):
    att = get_object_or_404(Attendance, pk=pk)
    if request.method == 'POST':
        att.delete()
        messages.success(request, 'Attendance deleted successfully!')
        return redirect('Ems_app:attendance_list')
    return render(request, 'hr/attendance_confirm_delete.html',
                  {'attendance': att})


# ============================================================
# EMPLOYEE — Monthly Attendance Detail (Admin/HR view)
# ============================================================
@login_required
@admin_or_hr_required
def employee_monthly_attendance(request, pk):
    """
    Show one employee's full month attendance.
    URL: /dashboard/attendance/employee/<pk>/?month=9&year=2026
    """
    employee = get_object_or_404(Employee, pk=pk)
    today = timezone.localdate()

    # ---------- Resolve month/year ----------
    try:
        month = int(request.GET.get('month') or today.month)
        if month < 1 or month > 12:
            month = today.month
    except (ValueError, TypeError):
        month = today.month
    try:
        year = int(request.GET.get('year') or today.year)
        if year < 2000 or year > 2100:
            year = today.year
    except (ValueError, TypeError):
        year = today.year

    month_name = calendar.month_name[month]

    # ---------- Attendance for this employee + month ----------
    attendances = Attendance.objects.filter(
        employee=employee,
        date__month=month,
        date__year=year,
    ).order_by('date')

    # ---------- Summary counts ----------
    summary = {
        'present':  attendances.filter(status='present').count(),
        'absent':   attendances.filter(status='absent').count(),
        'late':     attendances.filter(status='late').count(),
        'half_day': attendances.filter(status='half_day').count(),
        'leave':    attendances.filter(status='leave').count(),
    }

    # ---------- Total working hours ----------
    total_minutes = sum(a.working_minutes for a in attendances)
    total_hours = total_minutes // 60
    total_mins  = total_minutes % 60
    total_working_hours = f'{total_hours:02d}:{total_mins:02d}'

    # ---------- Leaves in this month ----------
    leaves = Leave.objects.filter(
        employee=employee,
        start_date__month=month,
        start_date__year=year,
    ).order_by('-applied_on')

    # ---------- Salary for this month (if exists) ----------
    salary = Salary.objects.filter(
        employee=employee,
        salary_month__month=month,
        salary_month__year=year,
    ).first()
    verified_attendances = attendances.filter(
        is_verified=True, check_in__isnull=False, check_out__isnull=False,
    )
    verified_paid_days = (
        verified_attendances.filter(status__in=['present', 'late']).count()
        + verified_attendances.filter(status='half_day').count() * 0.5
    )
    month_working_days = _get_month_working_days(year, month)
    monthly_base_salary = salary.basic_salary if salary else employee.current_salary
    verified_attendance_salary = round(
        float(monthly_base_salary or 0) * verified_paid_days / month_working_days, 2,
    ) if month_working_days else 0

    # ---------- Dropdown choices ----------
    all_years = list(range(today.year - 3, today.year + 2))
    all_months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    context = {
        'employee': employee,
        'attendances': attendances,
        'summary': summary,
        'total_working_hours': total_working_hours,
        'leaves': leaves,
        'salary': salary,
        'verified_attendance_salary': verified_attendance_salary,
        'pending_verification_count': attendances.filter(
            check_in__isnull=False, check_out__isnull=False, is_verified=False,
        ).count(),
        'month_label': f'{month_name} {year}',
        'selected_month': month,
        'selected_year': year,
        'all_months': all_months,
        'all_years': all_years,
    }
    return render(request, 'hr/employee_monthly_attendance.html', context)


# ============================================================
# ATTENDANCE BULK UPLOAD  (biometric sheet → per-employee calc)
# ============================================================
@login_required
@admin_or_hr_required
def attendance_bulk_upload(request):
    """
    Upload a biometric attendance sheet (.xlsx / .csv) and auto-create
    one Attendance row per (employee, date).
    Accepted columns (case-insensitive, any order):
        • employee identifier  → employee_code / pin / emp_id / email / employee
        • date                 → date / attendance_date / day
        • check_in             → check_in / in_time / intime / punch_in  (optional)
        • check_out            → check_out / out_time / outtime / punch_out (optional)
        • OR punch_time        → punch_time / time / punch  (multiple rows/day OK)
    Rules applied:
        • check-in ≤ 09:40              → Present
        • check-in  > 09:40              → Late
        • working hours < 4              → Half-Day
        • no check-in                    → Absent
        • existing "leave" records kept untouched
    """
    if request.method == 'POST':
        file = request.FILES.get('attendance_file')
        if not file:
            messages.error(request, 'Please select an attendance file.')
            return render(request, 'hr/attendance_bulk_upload.html')
        name = file.name.lower()
        rows_raw = []

        # Biometric exports commonly use labels such as "Employee ID" or
        # "Employee Code" instead of the snake_case names in our template.
        # Normalize every header to alphanumeric lowercase so these labels map
        # to the same keys for both CSV and Excel files.
        def normalize_header(value):
            return ''.join(char.lower() for char in str(value or '') if char.isalnum())

        try:
            if name.endswith('.csv'):
                wrapper = io.TextIOWrapper(file.file, encoding='utf-8-sig', newline='')
                for row in csv.DictReader(wrapper):
                    rows_raw.append({
                        normalize_header(k): v for k, v in row.items()
                    })
            elif name.endswith('.xlsx'):
                wb = load_workbook(file, data_only=True)
                ws = wb.active
                header = [normalize_header(c.value) for c in ws[1]]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    rows_raw.append(dict(zip(header, row)))
            else:
                messages.error(request, 'Only .xlsx or .csv files are supported.')
                return render(request, 'hr/attendance_bulk_upload.html')
        except Exception as e:
            messages.error(request, f'Could not read file: {e}')
            return render(request, 'hr/attendance_bulk_upload.html')

        if not rows_raw:
            messages.warning(request, 'The file has no data rows.')
            return render(request, 'hr/attendance_bulk_upload.html')

        # ---- Group punches per (employee, date) --------------------
        punches = {}
        errors = []
        for idx, row in enumerate(rows_raw, start=2):
            # Employee identifier
            emp_key = None
            for col in ('employeecode', 'empcode', 'code', 'pin', 'badgeid',
                        'employeeid', 'empid', 'staffid', 'email', 'employee'):
                v = row.get(col)
                if v is not None and str(v).strip():
                    emp_key = str(v).strip()
                    break
            if not emp_key:
                errors.append(f'Row {idx}: missing employee identifier')
                continue

            # Date
            date_val = None
            for col in ('date', 'attendance_date', 'day'):
                if row.get(col):
                    date_val = row[col]
                    break
            if not date_val:
                errors.append(f'Row {idx}: missing date')
                continue
            parsed_date = _parse_date(date_val)
            if not parsed_date:
                errors.append(f'Row {idx}: invalid date "{date_val}"')
                continue

            # Resolve employee
            emp = (
                Employee.objects.filter(employee_code__iexact=emp_key).first()
                or Employee.objects.filter(email__iexact=emp_key).first()
                or (Employee.objects.filter(emp_id=int(emp_key)).first()
                    if emp_key.isdigit() else None)
            )
            if not emp:
                errors.append(f'Row {idx}: employee "{emp_key}" not found')
                continue

            key = (emp.pk, parsed_date)
            bucket = punches.setdefault(key, {
                'employee': emp,
                'date': parsed_date,
                'ins': [],
                'outs': [],
            })

            # Check-in / check-out columns
            in_val = out_val = punch_val = None
            for c in ('check_in', 'in_time', 'in', 'intime', 'time_in', 'punch_in'):
                if row.get(c):
                    in_val = row[c]; break
            for c in ('check_out', 'out_time', 'out', 'outtime', 'time_out', 'punch_out'):
                if row.get(c):
                    out_val = row[c]; break
            for c in ('punch_time', 'time', 'punch', 'punchtime'):
                if row.get(c):
                    punch_val = row[c]; break

            if in_val:
                dt = _parse_datetime(in_val, parsed_date)
                if dt: bucket['ins'].append(dt)
            if out_val:
                dt = _parse_datetime(out_val, parsed_date)
                if dt: bucket['outs'].append(dt)
            if punch_val and not (in_val or out_val):
                # generic punch → treated as both in & out, min/max decides
                dt = _parse_datetime(punch_val, parsed_date)
                if dt:
                    bucket['ins'].append(dt)
                    bucket['outs'].append(dt)

        # ---- Build Attendance rows --------------------------------
        created = updated = skipped = 0
        for (emp_pk, att_date), data in punches.items():
            emp = data['employee']
            all_punches = sorted(set(data['ins'] + data['outs']))
            if len(all_punches) >= 2:
                check_in  = all_punches[0]
                check_out = all_punches[-1]
            elif len(all_punches) == 1:
                check_in, check_out = all_punches[0], None
            else:
                check_in = check_out = None

            # Status
            if check_in:
                status = 'present' if check_in.time() <= OFFICE_GRACE_END else 'late'
                if check_out:
                    hours = (check_out - check_in).total_seconds() / 3600
                    if hours < 4:
                        status = 'half_day'
            else:
                status = 'absent'

            existing = Attendance.objects.filter(employee=emp, date=att_date).first()
            if existing and existing.status == 'leave':
                skipped += 1
                continue

            _, is_new = Attendance.objects.update_or_create(
                employee=emp, date=att_date,
                defaults={
                    'check_in': check_in,
                    'check_out': check_out,
                    'status': status,
                    'is_verified': False,
                    'verified_by': None,
                    'verified_at': None,
                },
            )
            if is_new: created += 1
            else:      updated += 1

        messages.success(
            request,
            f'Attendance processed — Created: {created}, Updated: {updated}, '
            f'Skipped (on leave): {skipped}.',
        )
        if errors:
            preview = '; '.join(errors[:5])
            more = f' (+{len(errors) - 5} more)' if len(errors) > 5 else ''
            messages.warning(request, f'{len(errors)} row(s) skipped. {preview}{more}')
        return redirect('Ems_app:attendance_list')
    return render(request, 'hr/attendance_bulk_upload.html')


# ============================================================
# DOWNLOAD SAMPLE TEMPLATE
# ============================================================
@login_required
@admin_or_hr_required
def attendance_download_template(request):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Attendance'
    headers = ['employee_code', 'email', 'date', 'check_in', 'check_out']
    ws.append(headers)
    ws.append(['EMP001', 'john@example.com', '2026-10-01', '09:15', '18:30'])
    ws.append(['EMP001', 'john@example.com', '2026-10-02', '09:50', '18:40'])
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='1976D2')
    for c in ws[1]:
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal='center')
    for i, w in enumerate([18, 30, 14, 12, 12], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = (
        'attachment; filename="attendance_template.xlsx"'
    )
    return response


# ============================================================
# PUNCH-MACHINE API
# ============================================================
@csrf_exempt
@require_POST
def device_punch(request):
    token = request.headers.get('X-Device-Token', '')
    if not hmac.compare_digest(token, settings.DEVICE_TOKEN):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        payload = json.loads(request.body.decode('utf-8'))
        pin = str(payload['pin']).strip()
        punch_time = datetime.strptime(payload['time'], '%Y-%m-%d %H:%M:%S')
        punch_time = timezone.make_aware(punch_time)
        status = payload.get('status', 'in')
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'invalid payload'}, status=400)
    employee = (
        Employee.objects.filter(emp_id=pin).first()
        or Employee.objects.filter(employee_code__iexact=pin).first()
        or Employee.objects.filter(email__iexact=pin).first()
        or Employee.objects.filter(user__username__iexact=pin).first()
    )
    if not employee:
        return JsonResponse({'error': f'unknown employee: {pin}'}, status=404)
    date = punch_time.date()
    att, _ = Attendance.objects.get_or_create(
        employee=employee, date=date,
        defaults={'status': 'absent'},
    )
    if status == 'in':
        if not att.check_in:
            punch_clock = punch_time.time()
            if punch_clock <= OFFICE_GRACE_END:
                att.status = 'present'
            else:
                att.status = 'late'
            att.check_in = punch_time
    elif status == 'out':
        att.check_out = punch_time
        if att.status == 'absent' and att.check_in:
            att.status = 'late'
    att.save()
    return JsonResponse({
        'ok': True,
        'employee': employee.emp_name,
        'date': str(date),
        'status': att.status,
        'check_in': att.check_in.strftime('%H:%M') if att.check_in else None,
        'check_out': att.check_out.strftime('%H:%M') if att.check_out else None,
    })


# ============================================================
# LEAVE
# ============================================================
@login_required
def leave_list(request):
    if request.user.profile.role in ['admin', 'hr']:
        leaves = Leave.objects.all().order_by('-applied_on')
        template = 'hr/leave_list.html'
    else:
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, 'No employee record linked to your account.')
            return redirect('Accounts:logout')
        leaves = Leave.objects.filter(employee=employee).order_by('-applied_on')
        template = 'employee/leave_list.html'
    return render(request, template, {
        'leaves': leaves,
        'pending_count': leaves.filter(status='pending').count(),
        'approved_count': leaves.filter(status='approved').count(),
        'rejected_count': leaves.filter(status='rejected').count(),
    })


@login_required
@employee_required
def leave_apply(request):
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, 'No employee record linked to your account.')
        return redirect('Accounts:logout')
    if request.method == 'POST':
        form = LeaveForm(request.POST, request.FILES)
        if form.is_valid():
            leave = form.save(commit=False)
            leave.employee = employee
            leave.save()
            messages.success(request, 'Leave application submitted successfully!')
            return redirect('Ems_app:leave_list')
    else:
        form = LeaveForm()
    return render(request, 'employee/leave_apply.html', {'form': form})


@login_required
@admin_or_hr_required
def leave_detail(request, pk):
    leave = get_object_or_404(Leave, pk=pk)
    if request.method == 'POST':
        response = (request.POST.get('review_response') or '').strip()
        if not response:
            messages.error(request, 'Enter a reply before saving it.')
        else:
            leave.review_response = response
            leave.save(update_fields=['review_response', 'updated_at'])
            messages.success(request, 'Reply saved and will be visible to the employee.')
            return redirect('Ems_app:leave_detail', pk=leave.pk)
    return render(request, 'hr/leave_detail.html', {'leave': leave})


@login_required
@admin_or_hr_required
def leave_approve(request, pk):
    leave = get_object_or_404(Leave, pk=pk)
    if request.method == 'POST':
        deduction = 0
        is_excess = False
        if leave.leave_type == 'casual':
            month = leave.start_date.month
            year = leave.start_date.year
            already_approved = Leave.objects.filter(
                employee=leave.employee,
                leave_type='casual',
                status='approved',
                start_date__month=month,
                start_date__year=year,
            ).exclude(pk=leave.pk).count()
            total_after = already_approved + 1
            if total_after > CASUAL_LEAVE_LIMIT_PER_MONTH:
                is_excess = True
                emp = leave.employee
                daily = (emp.current_salary or 0) / 30
                deduction = round(float(daily) * leave.days, 2)
        leave.status = 'approved'
        leave.reviewed_by = request.user
        leave.is_excess_leave = is_excess
        leave.salary_deduction = deduction
        leave.save()
        if is_excess:
            messages.warning(request, f'Leave approved with salary deduction ₹{deduction:.2f} (exceeds limit of {CASUAL_LEAVE_LIMIT_PER_MONTH}).')
        else:
            messages.success(request, 'Leave approved successfully!')
        return redirect('Ems_app:leave_list')
    return render(request, 'hr/leave_approve.html', {'leave': leave})


@login_required
@admin_or_hr_required
def leave_reject(request, pk):
    leave = get_object_or_404(Leave, pk=pk)
    if request.method == 'POST':
        leave.status = 'rejected'
        leave.reviewed_by = request.user
        leave.save()
        messages.success(request, 'Leave rejected successfully!')
        return redirect('Ems_app:leave_list')
    return render(request, 'hr/leave_reject.html', {'leave': leave})


# ============================================================
# SALARY
# ============================================================
@login_required
def salary_list(request):
    is_admin_or_hr = request.user.profile.role in ['admin', 'hr']
    if is_admin_or_hr:
        salaries = Salary.objects.all().select_related(
            'employee', 'employee__department',
        ).prefetch_related('employee__attendances', 'payslip_request')
        employees = Employee.objects.all().order_by('emp_name')
        template = 'hr/salary_list.html'
    else:
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, 'No employee record linked to your account.')
            return redirect('Accounts:logout')
        salaries = Salary.objects.filter(employee=employee).select_related('employee').prefetch_related('employee__attendances', 'payslip_request')
        employees = Employee.objects.filter(pk=employee.pk)
        template = 'employee/salary_list.html'

    month    = request.GET.get('month') or ''
    year     = request.GET.get('year') or ''
    emp_name = (request.GET.get('employee_name') or '').strip()

    if is_admin_or_hr and emp_name:
        salaries = salaries.filter(employee__emp_name__icontains=emp_name)
    if month:
        salaries = salaries.filter(salary_month__month=int(month))
    if year:
        salaries = salaries.filter(salary_month__year=int(year))
    salaries = list(salaries.order_by('-salary_month', 'employee__emp_name'))
    for salary in salaries:
        _, salary.calculated_net_salary = _verified_attendance_pay(salary)

    current_year = timezone.now().year
    years = list(range(current_year - 3, current_year + 2))
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    return render(request, template, {
        'salaries': salaries,
        'employees': employees,
        'selected_month': month,
        'selected_year': year,
        'employee_name_q': emp_name,
        'months': months,
        'years': years,
    })


@login_required
@employee_required
@require_POST
def payslip_request_create(request, pk):
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, 'No employee record linked to your account.')
        return redirect('Ems_app:salary_list')
    salary = get_object_or_404(Salary, pk=pk, employee=employee)
    slip_request, created = PayslipRequest.objects.get_or_create(salary=salary)
    if not created and slip_request.status != 'approved':
        slip_request.status = 'pending'
        slip_request.response = ''
        slip_request.reviewed_at = None
        slip_request.reviewed_by = None
        slip_request.save(update_fields=['status', 'response', 'reviewed_at', 'reviewed_by'])
    messages.success(request, 'Payslip access request sent to HR.')
    return redirect('Ems_app:salary_list')


@login_required
@admin_or_hr_required
def payslip_request_list(request):
    requests = PayslipRequest.objects.select_related(
        'salary', 'salary__employee', 'salary__employee__department', 'reviewed_by',
    ).order_by('status', '-requested_at')
    return render(request, 'hr/payslip_requests.html', {'payslip_requests': requests})


@login_required
@admin_or_hr_required
@require_POST
def payslip_request_review(request, pk, decision):
    if decision not in ('approve', 'reject'):
        return redirect('Ems_app:payslip_request_list')
    slip_request = get_object_or_404(PayslipRequest, pk=pk)
    slip_request.status = 'approved' if decision == 'approve' else 'rejected'
    slip_request.response = (request.POST.get('response') or '').strip()
    slip_request.reviewed_by = request.user
    slip_request.reviewed_at = timezone.now()
    slip_request.save(update_fields=['status', 'response', 'reviewed_by', 'reviewed_at'])
    messages.success(request, f'Payslip request {slip_request.status}.')
    return redirect('Ems_app:payslip_request_list')


@login_required
@admin_or_hr_required
def salary_create(request):
    if request.method == 'POST':
        form = SalaryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Salary record created successfully!')
            return redirect('Ems_app:salary_list')
    else:
        form = SalaryForm()
    return render(request, 'hr/salary_form.html',
                  {'form': form, 'title': 'Add Salary'})


@login_required
@admin_or_hr_required
def salary_edit(request, pk):
    salary = get_object_or_404(Salary, pk=pk)
    if request.method == 'POST':
        form = SalaryForm(request.POST, instance=salary)
        if form.is_valid():
            form.save()
            messages.success(request, 'Salary record updated successfully!')
            return redirect('Ems_app:salary_list')
    else:
        form = SalaryForm(instance=salary)
    return render(request, 'hr/salary_form.html',
                  {'form': form, 'title': 'Edit Salary'})


@login_required
@admin_or_hr_required
def salary_delete(request, pk):
    salary = get_object_or_404(Salary, pk=pk)
    if request.method == 'POST':
        salary.delete()
        messages.success(request, 'Salary record deleted successfully!')
        return redirect('Ems_app:salary_list')
    _, salary.calculated_net_salary = _verified_attendance_pay(salary)
    return render(request, 'hr/salary_confirm_delete.html', {'salary': salary})


@login_required
def salary_payslip(request, pk):
    """Render salary slip as HTML page (view + print)."""
    salary = get_object_or_404(Salary, pk=pk)
    employee = salary.employee

    # ---------- Access control ----------
    if request.user.profile.role not in ['admin', 'hr']:
        if not hasattr(request.user, 'employee') or request.user.employee.pk != employee.pk:
            messages.error(request, 'You are not allowed to view this payslip.')
            return redirect('Ems_app:salary_list')
        if not PayslipRequest.objects.filter(salary=salary, status='approved').exists():
            messages.error(request, 'HR approval is required before viewing this payslip.')
            return redirect('Ems_app:salary_list')

    # ---------- Month start / end ----------
    month_start = salary.salary_month.replace(day=1)
    if salary.salary_month.month == 12:
        next_month_start = date(salary.salary_month.year + 1, 1, 1)
    else:
        next_month_start = date(salary.salary_month.year, salary.salary_month.month + 1, 1)
    month_end = next_month_start - timedelta(days=1)

    # ---------- WORKING DAYS (Mon-Sat, minus Sundays + 2nd Sat) ----------
    working_days_in_month = _get_month_working_days(
        salary.salary_month.year,
        salary.salary_month.month,
    )

    # ---------- Attendance aggregation ----------
    month_qs = Attendance.objects.filter(
        employee=employee,
        date__gte=month_start,
        date__lte=month_end,
        is_verified=True,
        check_in__isnull=False,
        check_out__isnull=False,
    )
    present_days = month_qs.filter(status='present').count()
    late_days    = month_qs.filter(status='late').count()
    half_days    = month_qs.filter(status='half_day').count()
    absent_days  = month_qs.filter(status='absent').count()

    # ---------- Total working hours ----------
    total_minutes = sum(a.working_minutes for a in month_qs)
    total_hours = total_minutes // 60
    total_mins  = total_minutes % 60
    total_working_hours = f'{total_hours:02d}:{total_mins:02d}'

    # ---------- Leave days (clamped to month) ----------
    leave_days = 0
    for lv in Leave.objects.filter(
        employee=employee,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    ):
        overlap_start = max(lv.start_date, month_start)
        overlap_end   = min(lv.end_date, month_end)
        leave_days += (overlap_end - overlap_start).days + 1

    # ---------- PAID DAYS: Present+Late=full day, Half-Day=0.5, Absent=0 ----------
    paid_days = round((present_days + late_days) + (half_days * 0.5), 1)
    paid_days = min(paid_days, working_days_in_month)  # cap at working days
    per_day_rate = round(float(salary.basic_salary) / working_days_in_month, 2) if working_days_in_month else 0
    attendance_salary = round(per_day_rate * paid_days, 2)
    total_earnings = round(attendance_salary + float(salary.bonus), 2)
    _, calculated_net_salary = _verified_attendance_pay(salary)

    context = {
        'salary': salary,
        'employee': employee,
        'present_days': present_days,
        'late_days': late_days,
        'half_days': half_days,
        'absent_days': absent_days,
        'leave_days': leave_days,
        'total_working_days': working_days_in_month,   # Mon-Sat − Sun − 2nd Sat
        'paid_days': paid_days,
        'per_day_rate': per_day_rate,
        'attendance_salary': attendance_salary,
        'total_earnings': total_earnings,
        'calculated_net_salary': calculated_net_salary,
        'pending_verification_count': Attendance.objects.filter(
            employee=employee, date__gte=month_start, date__lte=month_end,
            check_in__isnull=False, check_out__isnull=False, is_verified=False,
        ).count(),
        'total_working_hours': total_working_hours,
        'generated_on': timezone.now(),
    }

    if request.user.profile.role in ['admin', 'hr']:
        template_name = 'hr/salary_payslip.html'
    else:
        template_name = 'employee/salary_payslip.html'
    return render(request, template_name, context)


# ============================================================
# SALARY PAYSLIP — PDF DOWNLOAD
# ============================================================
@login_required
def salary_payslip_pdf(request, pk):
    """
    Generate and download salary slip as PDF.
    URL: /dashboard/salaries/<pk>/payslip/pdf/
    """
    salary = get_object_or_404(Salary, pk=pk)
    employee = salary.employee

    # ---------- Access control ----------
    if request.user.profile.role not in ['admin', 'hr']:
        if not hasattr(request.user, 'employee') or request.user.employee.pk != employee.pk:
            messages.error(request, 'You are not allowed to view this payslip.')
            return redirect('Ems_app:salary_list')
        if not PayslipRequest.objects.filter(salary=salary, status='approved').exists():
            messages.error(request, 'HR approval is required before downloading this payslip.')
            return redirect('Ems_app:salary_list')

    # ---------- Month start / end ----------
    month_start = salary.salary_month.replace(day=1)
    if salary.salary_month.month == 12:
        next_month_start = date(salary.salary_month.year + 1, 1, 1)
    else:
        next_month_start = date(salary.salary_month.year, salary.salary_month.month + 1, 1)
    month_end = next_month_start - timedelta(days=1)

    # ---------- WORKING DAYS (Mon-Sat, minus Sundays + 2nd Sat) ----------
    working_days_in_month = _get_month_working_days(
        salary.salary_month.year,
        salary.salary_month.month,
    )

    # ---------- Attendance aggregation ----------
    month_qs = Attendance.objects.filter(
        employee=employee,
        date__gte=month_start,
        date__lte=month_end,
        is_verified=True,
        check_in__isnull=False,
        check_out__isnull=False,
    )
    present_days = month_qs.filter(status='present').count()
    late_days    = month_qs.filter(status='late').count()
    half_days    = month_qs.filter(status='half_day').count()
    absent_days  = month_qs.filter(status='absent').count()
    total_minutes = sum(a.working_minutes for a in month_qs)
    total_hours = total_minutes // 60
    total_mins  = total_minutes % 60
    total_working_hours = f'{total_hours:02d}:{total_mins:02d}'

    # ---------- Leave days (clamped to month) ----------
    leave_days = 0
    for lv in Leave.objects.filter(
        employee=employee,
        status='approved',
        start_date__lte=month_end,
        end_date__gte=month_start,
    ):
        overlap_start = max(lv.start_date, month_start)
        overlap_end   = min(lv.end_date, month_end)
        leave_days += (overlap_end - overlap_start).days + 1

    # ---------- PAID DAYS: Present+Late=full day, Half-Day=0.5, Absent=0 ----------
    paid_days = round((present_days + late_days) + (half_days * 0.5), 1)
    paid_days = min(paid_days, working_days_in_month)  # cap at working days
    per_day_rate = round(float(salary.basic_salary) / working_days_in_month, 2) if working_days_in_month else 0
    attendance_salary = round(per_day_rate * paid_days, 2)
    _, calculated_net_salary = _verified_attendance_pay(salary)

    # ---------- Build PDF ----------
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f'Payslip - {employee.emp_name} - {salary.salary_month.strftime("%B %Y")}',
        author='Knovista Technologies',
    )

    # ---------- COLORS ----------
    C_NAVY        = colors.HexColor('#0B1F3A')
    C_ACCENT      = colors.HexColor('#2563EB')
    C_ACCENT_2    = colors.HexColor('#1D4ED8')
    C_ACCENT_SOFT = colors.HexColor('#EFF6FF')
    C_SUCCESS     = colors.HexColor('#059669')
    C_BG_SOFT     = colors.HexColor('#F8FAFC')
    C_BORDER      = colors.HexColor('#E2E8F0')
    C_TEXT        = colors.HexColor('#0F172A')
    C_MUTED       = colors.HexColor('#64748B')
    C_FAINT       = colors.HexColor('#94A3B8')

    styles = getSampleStyleSheet()

    # ---------- Styles ----------
    company_style = ParagraphStyle(
        'Company', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=17,
        textColor=C_NAVY, leading=20, spaceAfter=3,
    )
    company_info_style = ParagraphStyle(
        'CompanyInfo', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8.5,
        textColor=C_MUTED, leading=12,
    )
    payslip_label_style = ParagraphStyle(
        'PayslipLabel', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=22,
        textColor=C_ACCENT, alignment=2, leading=24, spaceAfter=4,
    )
    payslip_meta_style = ParagraphStyle(
        'PayslipMeta', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8.5,
        textColor=C_MUTED, alignment=2, leading=12,
    )
    title_style = ParagraphStyle(
        'Title', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=11,
        textColor=C_NAVY, alignment=TA_CENTER,
        spaceBefore=4, spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'Subtitle', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8,
        textColor=C_FAINT, alignment=TA_CENTER,
        spaceAfter=14,
    )
    box_head_style = ParagraphStyle(
        'BoxHead', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8,
        textColor=C_ACCENT, spaceAfter=6,
    )
    label_style = ParagraphStyle(
        'Label', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8.5,
        textColor=C_MUTED, leading=14,
    )
    value_style = ParagraphStyle(
        'Value', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8.5,
        textColor=C_TEXT, alignment=2, leading=14,
    )
    footer_style = ParagraphStyle(
        'Footer', parent=styles['Normal'],
        fontName='Helvetica', fontSize=7.5,
        textColor=C_FAINT, alignment=TA_CENTER, leading=10,
    )

    story = []

    # ============================================================
    # HEADER
    # ============================================================
    header_left = [
        Paragraph('Knovista Technologies', company_style),
        Paragraph(
            'Lulu Twin Tower · Infopark, Kochi, India<br/>'
            'info@knovista.in &nbsp;·&nbsp; +91 80 1234 5678',
            company_info_style,
        ),
    ]
    header_right = [
        Paragraph('PAYSLIP', payslip_label_style),
        Paragraph(
            f'<b>{salary.salary_month.strftime("%B %Y")}</b><br/>'
            f'No: <b>PS-{salary.salary_id:04d}</b><br/>'
            f'Issued: {timezone.localtime().strftime("%d %b %Y")}',
            payslip_meta_style,
        ),
    ]
    header_table = Table(
        [[header_left, header_right]],
        colWidths=[105 * mm, 75 * mm],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(header_table)

    # ---- Accent divider ----
    divider = Table(
        [[''], ['']],
        colWidths=[180 * mm],
        rowHeights=[2.5, 0.6],
    )
    divider.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_ACCENT),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#93C5FD')),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(divider)
    story.append(Spacer(1, 14))

    # ============================================================
    # TITLE
    # ============================================================
    story.append(Paragraph(
        f'SALARY SLIP FOR {salary.salary_month.strftime("%B %Y").upper()}',
        title_style,
    ))
    story.append(Paragraph(
        'AUTO-GENERATED · CONFIDENTIAL · KEEP FOR YOUR RECORDS',
        subtitle_style,
    ))

    # ============================================================
    # TWO INFO BOXES
    # ============================================================
    def make_row(label, value):
        return [Paragraph(label, label_style), Paragraph(value, value_style)]

    # ---------- LEFT BOX: Employee Details ----------
    emp_rows = [
        [Paragraph('👤  EMPLOYEE DETAILS', box_head_style), ''],
        make_row('Employee Name', employee.emp_name),
        make_row('Employee ID', employee.employee_code or str(employee.emp_id)),
        make_row('Designation', employee.designation or '—'),
        make_row('Department', employee.department.dept_name if employee.department else '—'),
        make_row('Date of Joining',
                 employee.joining_date.strftime('%d %b %Y') if employee.joining_date else '—'),
        make_row('Email', employee.email),
    ]
    emp_box = Table(
        emp_rows,
        colWidths=[30 * mm, 53 * mm],
        rowHeights=[None, 8 * mm, 8 * mm, 8 * mm, 8 * mm, 8 * mm, 8 * mm],
    )
    emp_box.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 0)),
        ('BACKGROUND', (0, 0), (-1, -1), C_BG_SOFT),
        ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEBEFORE', (0, 0), (0, -1), 3, C_ACCENT),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
    ]))

    # ---------- RIGHT BOX: Attendance & Payment (5 fields) ----------
    att_rows = [
        [Paragraph('📅  ATTENDANCE & PAYMENT', box_head_style), ''],
        make_row('Working Days', str(working_days_in_month)),
        make_row('Salary Month', salary.salary_month.strftime('%B %Y')),
        make_row('Paid Days', str(paid_days)),
        make_row('Payment Date', salary.updated_at.strftime('%d %b %Y')),
        make_row('Status', 'Paid'),
        make_row('', ''),
    ]
    att_box = Table(
        att_rows,
        colWidths=[30 * mm, 53 * mm],
        rowHeights=[None, 8 * mm, 8 * mm, 8 * mm, 8 * mm, 8 * mm, 8 * mm],
    )
    att_box.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 0)),
        ('BACKGROUND', (0, 0), (-1, -1), C_BG_SOFT),
        ('BOX', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEBEFORE', (0, 0), (0, -1), 3, C_SUCCESS),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
    ]))

    boxes_table = Table(
        [[emp_box, '', att_box]],
        colWidths=[83 * mm, 14 * mm, 83 * mm],
    )
    boxes_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(boxes_table)
    story.append(Spacer(1, 16))

    # ============================================================
    # SALARY BREAKDOWN
    # ============================================================
    sec_head = ParagraphStyle(
        'SecHead', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9.5,
        textColor=C_NAVY, spaceAfter=6,
    )
    story.append(Paragraph('💰  SALARY BREAKDOWN', sec_head))

    total_earnings = float(attendance_salary) + float(salary.bonus)

    def th(text, align='left'):
        return Paragraph(text, ParagraphStyle(
            'th', parent=styles['Normal'],
            fontName='Helvetica-Bold', fontSize=7.5,
            textColor=colors.white,
            alignment={'left': 0, 'right': 2, 'center': 1}[align],
            leading=10,
        ))

    def td(text, bold=False, align='left', color=None):
        return Paragraph(text, ParagraphStyle(
            'td', parent=styles['Normal'],
            fontName='Helvetica-Bold' if bold else 'Helvetica',
            fontSize=9,
            textColor=color or (C_TEXT if bold else C_MUTED),
            alignment={'left': 0, 'right': 2, 'center': 1}[align],
            leading=12,
        ))

    salary_table_data = [
        [
            th('EARNINGS'),
            th('AMOUNT (₹)', align='right'),
            th('DEDUCTIONS'),
            th('AMOUNT (₹)', align='right'),
        ],
        [
            td('Attendance Pay', bold=True, color=C_TEXT),
            td(f'{attendance_salary:,.2f}', bold=True, align='right', color=C_TEXT),
            td('Deductions', bold=True, color=C_TEXT),
            td(f'{salary.deductions:,.2f}', bold=True, align='right', color=C_TEXT),
        ],
        [
            td('Bonus'),
            td(f'{salary.bonus:,.2f}', align='right'),
            td('—'),
            td('0.00', align='right'),
        ],
        [
            td('TOTAL EARNINGS', bold=True, color=C_ACCENT_2),
            td(f'₹ {total_earnings:,.2f}', bold=True, align='right', color=C_ACCENT_2),
            td('TOTAL DEDUCTIONS', bold=True, color=colors.HexColor('#DC2626')),
            td(f'₹ {salary.deductions:,.2f}', bold=True, align='right',
               color=colors.HexColor('#DC2626')),
        ],
    ]
    salary_table = Table(
        salary_table_data,
        colWidths=[55 * mm, 35 * mm, 55 * mm, 35 * mm],
    )
    salary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 3), (-1, 3), colors.HexColor('#FEF3C7')),
        ('LINEABOVE', (0, 3), (-1, 3), 1.2, colors.HexColor('#F59E0B')),
        ('LINEBELOW', (0, 1), (-1, 2), 0.4, C_BORDER),
        ('BOX', (0, 0), (-1, -1), 0.6, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(salary_table)
    story.append(Spacer(1, 12))

    # ============================================================
    # NET PAY BOX
    # ============================================================
    net_left = Paragraph(
        f'<font size="9" color="#64748B"><b>NET PAYABLE AMOUNT</b></font><br/><br/>'
        f'<font size="24" color="#2563EB"><b>₹ {calculated_net_salary:,.2f}</b></font><br/>'
        f'<font size="8" color="#64748B">'
        f'<i>Net salary credited for {salary.salary_month.strftime("%B %Y")}</i>'
        f'</font>',
        styles['Normal'],
    )
    net_right = Paragraph(
        f'<font size="8" color="#64748B">'
        f'<b>Summary</b><br/><br/>'
        f'Gross Earnings&nbsp;&nbsp;&nbsp;<b>₹ {total_earnings:,.2f}</b><br/>'
        f'Total Deductions&nbsp;<b>₹ {salary.deductions:,.2f}</b><br/>'
        f'<font color="#2563EB"><b>Net Pay&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
        f'₹ {calculated_net_salary:,.2f}</b></font>'
        f'</font>',
        ParagraphStyle('NetRight', parent=styles['Normal'],
                       alignment=2, leading=13),
    )
    net_box = Table(
        [[net_left, net_right]],
        colWidths=[110 * mm, 70 * mm],
    )
    net_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_ACCENT_SOFT),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#BFDBFE')),
        ('LINEBEFORE', (0, 0), (0, -1), 5, C_ACCENT),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 18),
        ('RIGHTPADDING', (0, 0), (-1, -1), 18),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
    ]))
    story.append(net_box)
    story.append(Spacer(1, 18))

    # ============================================================
    # SIGNATURE LINES
    # ============================================================
    sig_data = [
        ['', ''],
        [
            Paragraph(
                '<font size="7.5" color="#94A3B8">Prepared by</font>',
                ParagraphStyle('sig1', parent=styles['Normal'], alignment=1),
            ),
            Paragraph(
                '<font size="7.5" color="#94A3B8">Authorized Signatory</font>',
                ParagraphStyle('sig2', parent=styles['Normal'], alignment=1),
            ),
        ],
    ]
    sig_table = Table(sig_data, colWidths=[90 * mm, 90 * mm], rowHeights=[16 * mm, 6 * mm])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('LINEABOVE', (0, 0), (0, 0), 0.5, C_FAINT),
        ('LINEABOVE', (1, 0), (1, 0), 0.5, C_FAINT),
        ('LEFTPADDING', (0, 0), (0, -1), 40),
        ('RIGHTPADDING', (0, 0), (0, -1), 40),
        ('LEFTPADDING', (1, 0), (1, -1), 40),
        ('RIGHTPADDING', (1, 0), (1, -1), 40),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 10))

    # ============================================================
    # FOOTER
    # ============================================================
    footer_divider = Table(
        [['']],
        colWidths=[180 * mm],
        rowHeights=[0.5],
    )
    footer_divider.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(footer_divider)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        'This is a computer-generated payslip and does not require a signature.<br/>'
        'For any queries, please contact <b>info@knovista.in</b> &nbsp;·&nbsp; '
        f'Generated on {timezone.localtime().strftime("%d %b %Y at %H:%M")}',
        footer_style,
    ))

    doc.build(story)
    buffer.seek(0)

    # ---------- Response ----------
    safe_name = employee.emp_name.replace(' ', '_')
    filename = f'Payslip_{safe_name}_{salary.salary_month.strftime("%B_%Y")}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ============================================================
# PROFILE
# ============================================================
@login_required
def profile(request):
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, 'No employee record linked to your account.')
        return redirect('Accounts:logout')
    if request.method == 'POST':
        employee.first_name = request.POST.get('first_name', employee.first_name)
        employee.last_name = request.POST.get('last_name', employee.last_name)
        employee.phone = request.POST.get('phone') or None
        employee.designation = request.POST.get('designation', employee.designation)
        employee.save()
        messages.success(request, 'Profile updated successfully!')
        return redirect('Ems_app:profile')
    return render(request, 'employee/profile.html', {'employee': employee})


# ============================================================
# DASHBOARDS
# ============================================================
@login_required
@employee_required
def employee_dashboard(request):
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, 'No employee record linked to your account.')
        return redirect('Accounts:logout')
    total_present = Attendance.objects.filter(employee=employee, status='present').count()
    total_absent = Attendance.objects.filter(employee=employee, status='absent').count()
    total_leaves = Leave.objects.filter(employee=employee).count()
    pending_leaves = Leave.objects.filter(employee=employee, status='pending').count()
    recent_attendance = Attendance.objects.filter(employee=employee).order_by('-date')[:5]
    recent_leaves = Leave.objects.filter(employee=employee).order_by('-applied_on')[:5]
    today = timezone.localdate()
    today_attendance = Attendance.objects.filter(employee=employee, date=today).first()
    today_leave = Leave.objects.filter(
        employee=employee, status='approved', start_date__lte=today, end_date__gte=today,
    ).first()
    month_start = today.replace(day=1)
    month_salary = Salary.objects.filter(
        employee=employee, salary_month__month=today.month, salary_month__year=today.year,
    ).first()
    month_attendance = Attendance.objects.filter(
        employee=employee, date__gte=month_start, date__lte=today,
        is_verified=True, check_in__isnull=False, check_out__isnull=False,
    )
    paid_days = (
        month_attendance.filter(status__in=['present', 'late']).count()
        + month_attendance.filter(status='half_day').count() * 0.5
    )
    working_days = _get_month_working_days(today.year, today.month)
    monthly_base_salary = month_salary.basic_salary if month_salary else employee.current_salary
    verified_month_salary = round(
        float(monthly_base_salary or 0) * paid_days / working_days, 2,
    ) if working_days else 0
    pending_verification_count = Attendance.objects.filter(
        employee=employee, date__gte=month_start, date__lte=today,
        check_in__isnull=False, check_out__isnull=False, is_verified=False,
    ).count()
    context = {
        'employee': employee,
        'total_present': total_present,
        'total_absent': total_absent,
        'total_leaves': total_leaves,
        'pending_leaves': pending_leaves,
        'recent_attendance': recent_attendance,
        'recent_leaves': recent_leaves,
        'today_attendance': today_attendance,
        'today_leave': today_leave,
        'today': today,
        'office_location_configured': bool(
            getattr(settings, 'COMPANY_LATITUDE', None)
            and getattr(settings, 'COMPANY_LONGITUDE', None)
        ),
        'verified_month_salary': verified_month_salary,
        'pending_verification_count': pending_verification_count,
        'salary_month_label': today.strftime('%B %Y'),
    }
    return render(request, 'employee/employee_dashboard.html', context)


@login_required
@hr_required
def hr_dashboard(request):
    today = timezone.localdate()
    total_employees = Employee.objects.count()
    present_count = Attendance.objects.filter(date=today, status='present').count()
    absent_count = Attendance.objects.filter(date=today, status='absent').count()
    pending_leaves = Leave.objects.filter(status='pending').count()
    recent_leaves = Leave.objects.filter(status='pending').order_by('-applied_on')[:5]
    recent_attendances = Attendance.objects.filter(date=today)[:5]
    self_employee = _linked_attendance_employee(request)
    self_attendance = Attendance.objects.filter(employee=self_employee, date=today).first() if self_employee else None
    self_leave = Leave.objects.filter(
        employee=self_employee, status='approved', start_date__lte=today, end_date__gte=today,
    ).first() if self_employee else None
    context = {
        'total_employees': total_employees,
        'present_count': present_count,
        'absent_count': absent_count,
        'pending_leaves': pending_leaves,
        'recent_leaves': recent_leaves,
        'recent_attendances': recent_attendances,
        'employee': self_employee,
        'clock_attendance': self_attendance,
        'on_leave': self_leave,
        'today': today,
        'office_location_configured': bool(
            getattr(settings, 'COMPANY_LATITUDE', None)
            and getattr(settings, 'COMPANY_LONGITUDE', None)
        ),
    }
    return render(request, 'hr/hr_dashboard.html', context)


# ============================================================
# REPORTS — Dashboard
# ============================================================
@login_required
@admin_or_hr_required
def reports(request):
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month') or today.month)
        if month < 1 or month > 12:
            month = today.month
    except (ValueError, TypeError):
        month = today.month
    try:
        year = int(request.GET.get('year') or today.year)
        if year < 2000 or year > 2100:
            year = today.year
    except (ValueError, TypeError):
        year = today.year

    month_name = calendar.month_name[month]
    month_start = date(year, month, 1)
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    month_end = next_month_start - timedelta(days=1)

    monthly_attendance = Attendance.objects.filter(
        date__gte=month_start, date__lte=month_end
    ).select_related('employee', 'employee__department').order_by('-date', 'employee__emp_name')

    attendance_summary = {
        'present':  monthly_attendance.filter(status='present').count(),
        'absent':   monthly_attendance.filter(status='absent').count(),
        'late':     monthly_attendance.filter(status='late').count(),
        'half_day': monthly_attendance.filter(status='half_day').count(),
    }
    attendance_details = {
        'present':  monthly_attendance.filter(status='present'),
        'absent':   monthly_attendance.filter(status='absent'),
        'late':     monthly_attendance.filter(status='late'),
        'half_day': monthly_attendance.filter(status='half_day'),
    }

    leave_qs = Leave.objects.filter(
        start_date__gte=month_start, start_date__lte=month_end,
    ).select_related('employee').order_by('-applied_on')
    leave_summary = {
        'pending':  leave_qs.filter(status='pending').count(),
        'approved': leave_qs.filter(status='approved').count(),
        'rejected': leave_qs.filter(status='rejected').count(),
    }
    leave_details = {
        'pending':  leave_qs.filter(status='pending'),
        'approved': leave_qs.filter(status='approved'),
        'rejected': leave_qs.filter(status='rejected'),
    }

    salary_qs = Salary.objects.filter(
        salary_month__year=year,
        salary_month__month=month,
    ).select_related('employee').prefetch_related('employee__attendances')
    salary_records = list(salary_qs)
    salary_summary = salary_qs.aggregate(
        total_salary=Sum('basic_salary'),
        total_bonus=Sum('bonus'),
        total_deductions=Sum('deductions'),
    )
    salary_summary['total_net'] = round(
        sum(_verified_attendance_pay(record)[1] for record in salary_records), 2,
    )

    dept_wise = Department.objects.annotate(emp_count=Count('employee'))
    all_years = list(range(today.year - 3, today.year + 2))
    all_months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    context = {
        'dept_wise': dept_wise,
        'attendance_summary': attendance_summary,
        'attendance_details': attendance_details,
        'leave_summary': leave_summary,
        'leave_details': leave_details,
        'salary_summary': salary_summary,
        'month_label': f'{month_name} {year}',
        'selected_month': month,
        'selected_year': year,
        'all_months': all_months,
        'all_years': all_years,
        'current_month': today.month,
        'current_year': today.year,
    }
    return render(request, 'admin/reports.html', context)


# ============================================================
# REPORTS — Download (Excel + PDF)
# ============================================================
@login_required
@admin_or_hr_required
def reports_download_monthly(request):
    today = timezone.now().date()
    try:
        month = int(request.GET.get('month') or today.month)
        if month < 1 or month > 12:
            month = today.month
    except (ValueError, TypeError):
        month = today.month
    try:
        year = int(request.GET.get('year') or today.year)
        if year < 2000 or year > 2100:
            year = today.year
    except (ValueError, TypeError):
        year = today.year

    export_format = (request.GET.get('format') or 'excel').lower()
    month_name = calendar.month_name[month]

    qs = Attendance.objects.filter(
        date__month=month,
        date__year=year,
    ).select_related('employee', 'employee__department')

    departments = Department.objects.all().order_by('dept_name')
    rows = []
    totals = {'present': 0, 'absent': 0, 'late': 0, 'half_day': 0, 'total': 0}

    for dept in departments:
        dept_qs = qs.filter(employee__department=dept)
        present  = dept_qs.filter(status='present').count()
        absent   = dept_qs.filter(status='absent').count()
        late     = dept_qs.filter(status='late').count()
        half_day = dept_qs.filter(status='half_day').count()
        total    = present + absent + late + half_day
        rows.append({
            'department': dept.dept_name,
            'present': present, 'absent': absent,
            'late': late, 'half_day': half_day, 'total': total,
        })
        totals['present']  += present
        totals['absent']   += absent
        totals['late']     += late
        totals['half_day'] += half_day
        totals['total']    += total

    no_dept_qs = qs.filter(employee__department__isnull=True)
    if no_dept_qs.exists():
        present  = no_dept_qs.filter(status='present').count()
        absent   = no_dept_qs.filter(status='absent').count()
        late     = no_dept_qs.filter(status='late').count()
        half_day = no_dept_qs.filter(status='half_day').count()
        total    = present + absent + late + half_day
        rows.append({
            'department': 'Unassigned',
            'present': present, 'absent': absent,
            'late': late, 'half_day': half_day, 'total': total,
        })
        totals['present']  += present
        totals['absent']   += absent
        totals['late']     += late
        totals['half_day'] += half_day
        totals['total']    += total

    generated_at = timezone.localtime().strftime('%d-%b-%Y %H:%M')
    title_text = f'Monthly Attendance Report — {month_name} {year}'
    subtitle_text = f'Department-wise summary · Generated on {generated_at}'

    # ---------- PDF EXPORT ----------
    if export_format == 'pdf':
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=landscape(A4),
            leftMargin=15 * mm, rightMargin=15 * mm,
            topMargin=15 * mm, bottomMargin=15 * mm,
            title=title_text, author='EMS · Knovista Technologies',
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'],
                                     fontSize=16, textColor=colors.HexColor('#0B1F3A'),
                                     alignment=TA_CENTER, spaceAfter=4)
        subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Normal'],
                                        fontSize=9, textColor=colors.HexColor('#666666'),
                                        alignment=TA_CENTER, spaceAfter=14)
        footer_style = ParagraphStyle('FooterStyle', parent=styles['Normal'],
                                      fontSize=8, textColor=colors.HexColor('#888888'),
                                      alignment=TA_CENTER)
        story = [
            Paragraph(title_text, title_style),
            Paragraph(subtitle_text, subtitle_style),
        ]
        table_data = [['Department', 'Present', 'Absent', 'Late', 'Half-Day', 'Total']]
        for r in rows:
            table_data.append([r['department'], str(r['present']), str(r['absent']),
                               str(r['late']), str(r['half_day']), str(r['total'])])
        if rows:
            table_data.append(['TOTAL', str(totals['present']), str(totals['absent']),
                               str(totals['late']), str(totals['half_day']), str(totals['total'])])
        else:
            table_data.append(['No attendance records for this month.', '', '', '', '', ''])

        table = Table(table_data,
                      colWidths=[90 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm],
                      repeatRows=1)
        table_styles = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1976D2')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (1, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CCCCCC')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]
        if rows:
            last = len(table_data) - 1
            table_styles += [
                ('BACKGROUND', (0, last), (-1, last), colors.HexColor('#059669')),
                ('TEXTCOLOR', (0, last), (-1, last), colors.white),
                ('FONTNAME', (0, last), (-1, last), 'Helvetica-Bold'),
                ('FONTSIZE', (0, last), (-1, last), 10),
            ]
        table.setStyle(TableStyle(table_styles))
        story.append(table)
        story.append(Spacer(1, 10 * mm))
        story.append(Paragraph('Generated by EMS · Knovista Technologies Pvt. Ltd.', footer_style))
        doc.build(story)
        buffer.seek(0)
        filename = f'Attendance_Report_{month_name}_{year}.pdf'
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    # ---------- EXCEL EXPORT ----------
    wb = Workbook()
    ws = wb.active
    ws.title = f'{month_name} {year}'

    title_font = Font(bold=True, size=14, color='FFFFFF')
    title_fill = PatternFill('solid', fgColor='0B1F3A')
    header_font = Font(bold=True, size=11, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='1976D2')
    total_font = Font(bold=True, size=11, color='FFFFFF')
    total_fill = PatternFill('solid', fgColor='059669')
    center_align = Alignment(horizontal='center', vertical='center')
    left_align = Alignment(horizontal='left', vertical='center')
    thin_border = Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='thin', color='CCCCCC'),
    )

    ws.merge_cells('A1:F1')
    t = ws['A1']
    t.value = title_text
    t.font = title_font
    t.fill = title_fill
    t.alignment = center_align
    ws.row_dimensions[1].height = 26

    ws.merge_cells('A2:F2')
    s = ws['A2']
    s.value = subtitle_text
    s.font = Font(italic=True, size=10, color='555555')
    s.alignment = center_align
    ws.row_dimensions[2].height = 18

    headers = ['Department', 'Present', 'Absent', 'Late', 'Half-Day', 'Total']
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=4, column=col_idx, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center_align
        c.border = thin_border
    ws.row_dimensions[4].height = 22

    start_row = 5
    for i, r in enumerate(rows):
        rn = start_row + i
        ws.cell(row=rn, column=1, value=r['department']).alignment = left_align
        ws.cell(row=rn, column=2, value=r['present']).alignment = center_align
        ws.cell(row=rn, column=3, value=r['absent']).alignment = center_align
        ws.cell(row=rn, column=4, value=r['late']).alignment = center_align
        ws.cell(row=rn, column=5, value=r['half_day']).alignment = center_align
        ws.cell(row=rn, column=6, value=r['total']).alignment = center_align
        for ci in range(1, 7):
            ws.cell(row=rn, column=ci).border = thin_border

    total_row = start_row + len(rows)
    if rows:
        ws.cell(row=total_row, column=1, value='TOTAL').font = total_font
        ws.cell(row=total_row, column=1).fill = total_fill
        ws.cell(row=total_row, column=1).alignment = left_align
        ws.cell(row=total_row, column=1).border = thin_border
        for idx, key in enumerate(['present', 'absent', 'late', 'half_day', 'total'], start=2):
            c = ws.cell(row=total_row, column=idx, value=totals[key])
            c.font = total_font
            c.fill = total_fill
            c.alignment = center_align
            c.border = thin_border
        ws.row_dimensions[total_row].height = 22
    else:
        ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=6)
        msg = ws.cell(row=start_row, column=1, value='No attendance records for this month.')
        msg.alignment = center_align
        msg.font = Font(italic=True, color='999999')

    footer_row = total_row + 2
    ws.merge_cells(start_row=footer_row, start_column=1, end_row=footer_row, end_column=6)
    f = ws.cell(row=footer_row, column=1, value='Generated by EMS · Knovista Technologies')
    f.font = Font(italic=True, size=9, color='888888')
    f.alignment = center_align

    for i, w in enumerate([30, 12, 12, 12, 12, 12], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A5'

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f'Attendance_Report_{month_name}_{year}.xlsx'
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
