from django import forms
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator
from datetime import datetime, date

from .models import Department, Employee, Attendance, Leave, Salary


# ============================================================
# REUSABLE PASSWORD VALIDATORS
# ============================================================
def password_validators():
    return [
        MinLengthValidator(8, message='Password must be at least 8 characters long.'),
    ]


# ============================================================
# DEPARTMENT
# ============================================================
class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['dept_name', 'description']
        widgets = {
            'dept_name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class ManualAttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ['employee', 'date', 'check_in', 'check_out', 'status', 'work_mode']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'check_in': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'check_out': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'work_mode': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {
            'employee': 'Employee',
            'date': 'Attendance date',
            'check_in': 'Check-in (24-hour time)',
            'check_out': 'Check-out (24-hour time)',
            'status': 'Attendance status',
            'work_mode': 'Work location',
        }

    def clean(self):
        cleaned = super().clean()
        check_in = cleaned.get('check_in')
        check_out = cleaned.get('check_out')
        if check_out and not check_in:
            self.add_error('check_in', 'Enter a check-in time before check-out.')
        elif check_in and check_out and check_out <= check_in:
            self.add_error('check_out', 'Check-out must be later than check-in.')
        return cleaned


# ============================================================
# EMPLOYEE (with validation + status)
# ============================================================
class EmployeeForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Minimum 8 characters',
        }),
        label="Password",
        required=False,
        validators=password_validators(),
        min_length=8,
        help_text="Leave blank to keep current password when editing. Minimum 8 characters.",
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm password',
        }),
        label="Confirm Password",
        required=False,
        validators=password_validators(),
        min_length=8,
    )

    class Meta:
        model = Employee
        fields = [
            'employee_code',
            'first_name', 'last_name',
            'email', 'phone',
            'date_of_birth',
            'department',
            'joining_date',
            'designation',
            'location',
            'experience',
            'current_salary',
            'cv',
            'status',              # NEW: active/inactive
        ]
        widgets = {
            'employee_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'EMP001'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'employee@company.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone number'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'department': forms.Select(attrs={'class': 'form-control'}),
            'joining_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'designation': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Software Engineer'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Operational Office'}),
            'experience': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 3 years'}),
            'current_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
            'cv': forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.doc,.docx'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Editing — password optional
            self.fields['password'].required = False
            self.fields['confirm_password'].required = False
        else:
            # Creating — password required
            self.fields['password'].required = True
            self.fields['confirm_password'].required = True

    # Reject future joining dates
    def clean_joining_date(self):
        d = self.cleaned_data.get('joining_date')
        if d and d > date.today():
            raise forms.ValidationError('Joining date cannot be a future date.')
        return d

    # Reject negative salary
    def clean_current_salary(self):
        s = self.cleaned_data.get('current_salary') or 0
        if s < 0:
            raise forms.ValidationError('Salary cannot be negative.')
        return s

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if password and len(password) < 8:
            raise forms.ValidationError('Password must be at least 8 characters long.')
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password or confirm_password:
            if password != confirm_password:
                raise forms.ValidationError("Passwords do not match.")
            if password and len(password) < 8:
                raise forms.ValidationError("Password must be at least 8 characters long.")

        # Department required
        if not cleaned_data.get('department'):
            raise forms.ValidationError('Department is required.')
        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip().lower()
            existing = Employee.objects.filter(email__iexact=email)
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("An employee with this email already exists.")
        return email

    def clean_employee_code(self):
        code = self.cleaned_data.get('employee_code')
        if code:
            code = code.strip().upper()
            existing = Employee.objects.filter(employee_code__iexact=code)
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("This employee code is already taken.")
        return code

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone:
            existing = Employee.objects.filter(phone=phone)
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("An employee with this phone number already exists.")
        return phone

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.first_name or instance.last_name:
            instance.emp_name = f"{instance.first_name} {instance.last_name}".strip()
        if commit:
            instance.save()
        return instance


# ============================================================
# HR (same as EmployeeForm but no status field)
# ============================================================
class HRForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Minimum 8 characters'}),
        validators=password_validators(),
        min_length=8,
        required=False,
        help_text="Minimum 8 characters. Leave blank when editing to keep the current password.",
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm password'}),
        validators=password_validators(),
        min_length=8,
        required=False,
    )

    class Meta:
        model = Employee
        fields = [
            'employee_code',
            'first_name', 'last_name',
            'email', 'phone',
            'date_of_birth',
            'department',
            'joining_date',
            'designation',
            'experience',
            'current_salary',
            'cv',
        ]
        widgets = {
            'employee_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'HR001'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'hr@company.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone number'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'department': forms.Select(attrs={'class': 'form-control'}),
            'joining_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'designation': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., HR Manager'}),
            'experience': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 5 years'}),
            'current_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
            'cv': forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.doc,.docx'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Editing — password is optional
            self.fields['password'].required = False
            self.fields['confirm_password'].required = False
            self.fields['password'].help_text = "Leave blank to keep the current password. Minimum 8 characters if changing."
        else:
            # Creating — password is required
            self.fields['password'].required = True
            self.fields['confirm_password'].required = True

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if password and len(password) < 8:
            raise forms.ValidationError('Password must be at least 8 characters long.')
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        if password or confirm_password:
            if password != confirm_password:
                raise forms.ValidationError("Passwords do not match.")
        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            email = email.strip().lower()
            existing = User.objects.filter(email__iexact=email)
            if self.instance and self.instance.pk and hasattr(self.instance, 'user') and self.instance.user_id:
                existing = existing.exclude(pk=self.instance.user_id)
            elif User.objects.filter(username__iexact=email).exists():
                raise forms.ValidationError('A user with this email already exists.')
            if existing.exists():
                raise forms.ValidationError('A user with this email already exists.')
        return email

    def clean_employee_code(self):
        code = self.cleaned_data.get('employee_code')
        if code:
            code = code.strip().upper()
            existing = Employee.objects.filter(employee_code__iexact=code)
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError("This employee code is already taken.")
        return code


# ============================================================
# LEAVE
# ============================================================
class LeaveForm(forms.ModelForm):
    class Meta:
        model = Leave
        fields = ['leave_type', 'start_date', 'end_date', 'reason', 'proof']
        widgets = {
            'leave_type': forms.Select(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'proof': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError('End date must be after start date.')
        return cleaned_data


# ============================================================
# SALARY
# ============================================================
class SalaryForm(forms.ModelForm):
    salary_month = forms.CharField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'month'}),
        label="Salary Month",
    )

    class Meta:
        model = Salary
        fields = ['employee', 'basic_salary', 'bonus', 'deductions', 'salary_month']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-control'}),
            'basic_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'bonus': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'deductions': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.salary_month:
            self.initial['salary_month'] = self.instance.salary_month.strftime('%Y-%m')

    def clean_salary_month(self):
        data = self.cleaned_data.get('salary_month')
        if not data:
            raise forms.ValidationError("Please select a month.")
        if hasattr(data, 'strftime'):
            return data.replace(day=1)
        if isinstance(data, str):
            data = data.strip()
            if len(data) == 7 and data[4] == '-':
                try:
                    dt = datetime.strptime(data, '%Y-%m')
                    return dt.date().replace(day=1)
                except ValueError:
                    pass
            if len(data) == 10:
                try:
                    dt = datetime.strptime(data, '%Y-%m-%d')
                    return dt.date()
                except ValueError:
                    pass
        raise forms.ValidationError("Invalid date format. Please select a valid month.")

    def clean(self):
        cleaned_data = super().clean()
        for field in ['basic_salary', 'bonus', 'deductions']:
            if field in cleaned_data and cleaned_data[field] is None:
                cleaned_data[field] = 0
        return cleaned_data
