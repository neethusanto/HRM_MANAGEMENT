
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.contrib.auth.password_validation import validate_password
import os

from .models import UserProfile
from Ems_app.models import Employee


# ============================================================
# BOOTSTRAP ADMIN
# ============================================================
ADMIN_EMAIL = os.environ.get('EMS_BOOTSTRAP_ADMIN_EMAIL', 'admin@ems.com')
ADMIN_PASSWORD = os.environ.get('EMS_BOOTSTRAP_ADMIN_PASSWORD')


# ============================================================
# HELPERS
# ============================================================

def _ensure_admin_profile_and_employee(user):
    """
    Ensure admin user has role='admin' and a linked Employee row.

    Safe against:
      • Employee already linked to this user  → reuse it
      • Employee exists with same email but different user → re-link
      • No Employee exists at all             → create one
    """
    # ---------- 1. Ensure profile with admin role ----------
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.role != 'admin':
        profile.role = 'admin'
        profile.save()

    # ---------- 2. Resolve email ----------
    email = (user.email or ADMIN_EMAIL).strip().lower()

    # ---------- 3. Try to find an existing Employee ----------
    employee = None

    # 3a. By linked user
    employee = Employee.objects.filter(user=user).first()

    # 3b. By email (case-insensitive)
    if employee is None:
        employee = Employee.objects.filter(email__iexact=email).first()

    # 3c. By username matching email
    if employee is None and user.username:
        employee = Employee.objects.filter(
            email__iexact=user.username
        ).first()

    # ---------- 4. Update or create ----------
    if employee is not None:
        changed = False
        if employee.user_id != user.id:
            employee.user = user
            changed = True
        if employee.email != email:
            clash = Employee.objects.filter(
                email__iexact=email
            ).exclude(pk=employee.pk).exists()
            if not clash:
                employee.email = email
                changed = True
        if changed:
            employee.save()
    else:
        Employee.objects.create(
            user=user,
            emp_name=user.first_name or 'Admin User',
            email=email,
            joining_date='2024-01-01',
            designation='System Administrator',
            is_hr=False,
        )


def _find_user_by_identifier(identifier):
    """Find user by email (preferred) or username (legacy fallback)."""
    if not identifier:
        return None
    identifier = identifier.strip()

    user = User.objects.filter(email__iexact=identifier).first()
    if user:
        return user

    return User.objects.filter(username__iexact=identifier).first()


def _authenticate_by_email(identifier, password):
    """Authenticate via email + password. Returns User or None."""
    user = _find_user_by_identifier(identifier)
    if user is None:
        return None
    if not user.check_password(password):
        return None
    if not user.is_active:
        return None
    return user


def _validate_new_password(password, user=None):
    """Validate a new password. Returns list of error messages."""
    try:
        validate_password(password, user=user)
        return []
    except ValidationError as e:
        return e.messages


# ============================================================
# UNIFIED LOGIN  (email only)
# ============================================================

def unified_login(request):
    if request.user.is_authenticated:
        return redirect('Accounts:dashboard_redirect')

    if request.method == 'POST':
        identifier = (
            request.POST.get('email')
            or request.POST.get('username')
            or ''
        ).strip()
        password = request.POST.get('password', '')

        if not identifier or not password:
            messages.error(request, 'Please enter your email and password.')
            return render(request, 'accounts/login.html')

        # ---------- Bootstrap admin shortcut ----------
        if ADMIN_PASSWORD and identifier.lower() == ADMIN_EMAIL.lower() and password == ADMIN_PASSWORD:
            user = User.objects.filter(email__iexact=ADMIN_EMAIL).first()
            if not user:
                user = User(
                    username=ADMIN_EMAIL,
                    email=ADMIN_EMAIL,
                    first_name='Admin',
                )
                user.set_password(ADMIN_PASSWORD)
                user.save()
            _ensure_admin_profile_and_employee(user)
            login(request, user)
            return redirect('Ems_app:admin_dashboard')

        # ---------- Normal email authentication ----------
        user = _authenticate_by_email(identifier, password)

        if user is None:
            exists = User.objects.filter(email__iexact=identifier).exists()
            if not exists:
                messages.error(request, f'No account found for "{identifier}".')
            else:
                messages.error(request, 'Incorrect password for this account.')
            return render(request, 'accounts/login.html')

        if not hasattr(user, 'profile'):
            messages.error(request, 'Your account has no profile. Contact admin.')
            return render(request, 'accounts/login.html')

        login(request, user)
        return redirect('Accounts:dashboard_redirect')

    return render(request, 'accounts/login.html')


# ============================================================
# LOGOUT / REDIRECT
# ============================================================

def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('Accounts:login_choice')


@login_required
def dashboard_redirect(request):
    if not hasattr(request.user, 'profile'):
        messages.error(request, 'Your account has no profile. Contact admin.')
        logout(request)
        return redirect('Accounts:login_choice')

    role = request.user.profile.role
    if role == 'admin':
        return redirect('Ems_app:admin_dashboard')
    elif role == 'hr':
        return redirect('Ems_app:hr_dashboard')
    else:
        return redirect('Ems_app:employee_dashboard')


# ============================================================
# BOOTSTRAP DEFAULT ADMIN
# ============================================================

def create_default_admin():
    """Create the bootstrap admin user with email-only login."""
    user = User.objects.filter(email__iexact=ADMIN_EMAIL).first()
    if not user:
        if not ADMIN_PASSWORD:
            raise ImproperlyConfigured(
                'Set EMS_BOOTSTRAP_ADMIN_PASSWORD before creating the bootstrap admin.'
            )
        user = User(
            username=ADMIN_EMAIL,
            email=ADMIN_EMAIL,
            first_name='Admin',
        )
        user.set_password(ADMIN_PASSWORD)
        user.save()
        _ensure_admin_profile_and_employee(user)

        print("=" * 50)
        print("Default admin created successfully!")
        print(f"Email:    {ADMIN_EMAIL}")
        print("Set a unique password in EMS_BOOTSTRAP_ADMIN_PASSWORD before deployment.")
        print("=" * 50)
    else:
        _ensure_admin_profile_and_employee(user)
