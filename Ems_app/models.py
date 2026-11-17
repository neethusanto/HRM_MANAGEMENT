from django.db import models
from django.contrib.auth.models import User


class Department(models.Model):
    dept_id = models.AutoField(primary_key=True)
    dept_name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.dept_name

    class Meta:
        db_table = 'department'


class Employee(models.Model):
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    emp_id = models.AutoField(primary_key=True)
    employee_code = models.CharField(
        max_length=20, unique=True, blank=True, null=True,
        help_text="Custom employee ID like EMP001",
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee')

    emp_name = models.CharField(max_length=100, blank=True, default='')
    first_name = models.CharField(max_length=50, blank=True, default='')
    last_name = models.CharField(max_length=50, blank=True, default='')

    email = models.EmailField(max_length=150, unique=True)
    phone = models.CharField(max_length=15, unique=True, blank=True, null=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)

    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    joining_date = models.DateField()
    designation = models.CharField(max_length=100, blank=True, null=True)
    location = models.CharField(max_length=120, default='Operational Office', blank=True)

    experience = models.CharField(max_length=50, blank=True, null=True)
    current_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cv = models.FileField(upload_to='cvs/', blank=True, null=True)

    # NEW: employee status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    is_hr = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.first_name or self.last_name:
            self.emp_name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.emp_name or self.email

    class Meta:
        db_table = 'employee'


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('half_day', 'Half-Day'),
        ('leave', 'Leave'),
    ]

    attendance_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField()
    check_in = models.DateTimeField(blank=True, null=True)
    check_out = models.DateTimeField(blank=True, null=True)
    work_mode = models.CharField(
        max_length=12,
        choices=[('office', 'Office'), ('home', 'Work from home')],
        default='office',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='absent')
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='verified_attendances',
    )
    verified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def working_hours(self):
        if self.check_in and self.check_out:
            delta = self.check_out - self.check_in
            total_seconds = int(delta.total_seconds())
            if total_seconds < 0:
                return '—'
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            if h == 0 and m == 0:
                return '0 min'
            if h == 0:
                return f'{m} min'
            if m == 0:
                return f'{h} hrs'
            return f'{h} hrs {m} min'
        return '—'

    @property
    def working_hours_hhmm(self):
        if self.check_in and self.check_out:
            delta = self.check_out - self.check_in
            total_seconds = int(delta.total_seconds())
            if total_seconds < 0:
                return '—'
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            return f'{h:02d}:{m:02d}'
        return '—'

    @property
    def working_minutes(self):
        if self.check_in and self.check_out:
            delta = self.check_out - self.check_in
            return max(0, int(delta.total_seconds() // 60))
        return 0

    def __str__(self):
        return f"{self.employee.emp_name} - {self.date} - {self.status}"

    class Meta:
        db_table = 'attendance'
        unique_together = ['employee', 'date']


class Leave(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('sick', 'Sick Leave'),
        ('casual', 'Casual Leave'),
        ('earned', 'Earned Leave'),
        ('unpaid', 'Unpaid Leave'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    leave_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leaves')
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True, null=True)
    review_response = models.TextField(blank=True, default='')
    proof = models.FileField(upload_to='leave_proofs/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    applied_on = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_leaves',
    )

    # NEW: salary deduction for excess casual leaves
    is_excess_leave = models.BooleanField(default=False)
    salary_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    @property
    def days(self):
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 0

    def __str__(self):
        return f"{self.employee.emp_name} - {self.leave_type} - {self.status}"

    class Meta:
        db_table = 'leave'


class Salary(models.Model):
    salary_id = models.AutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='salaries')
    basic_salary = models.DecimalField(max_digits=10, decimal_places=2)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    salary_month = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.net_salary = self.basic_salary + self.bonus - self.deductions
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.emp_name} - {self.salary_month.strftime('%B %Y')}"

    class Meta:
        db_table = 'salary'
        unique_together = ['employee', 'salary_month']


class PayslipRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    salary = models.OneToOneField(
        Salary, on_delete=models.CASCADE, related_name='payslip_request',
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    response = models.TextField(blank=True, default='')
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_payslip_requests',
    )

    def __str__(self):
        return f"Payslip request - {self.salary.employee.emp_name} - {self.salary.salary_month:%B %Y}"
