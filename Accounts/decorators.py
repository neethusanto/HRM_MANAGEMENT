# from django.shortcuts import redirect
# from django.contrib import messages


# def role_required(allowed_roles=None):
#     if allowed_roles is None:
#         allowed_roles = []

#     def decorator(view_func):
#         def wrapper(request, *args, **kwargs):
#             if not request.user.is_authenticated:
#                 return redirect('Accounts:login_choice')

#             if not hasattr(request.user, 'profile'):
#                 messages.error(request, 'Your account has no profile. Contact admin.')
#                 return redirect('Accounts:login_choice')

#             role = request.user.profile.role
#             if role not in allowed_roles:
#                 messages.error(request, 'You do not have permission to access this page.')
#                 return redirect('Accounts:dashboard_redirect')

#             return view_func(request, *args, **kwargs)
#         return wrapper
#     return decorator


# def admin_required(view_func):
#     return role_required(['admin'])(view_func)


# def hr_required(view_func):
#     return role_required(['hr'])(view_func)


# def employee_required(view_func):
#     return role_required(['employee'])(view_func)


# def admin_or_hr_required(view_func):
#     return role_required(['admin', 'hr'])(view_func)



from django.shortcuts import redirect
from django.contrib import messages


def role_required(allowed_roles=None):
    if allowed_roles is None:
        allowed_roles = []

    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('Accounts:login_choice')

            if not hasattr(request.user, 'profile'):
                messages.error(request, 'Your account has no profile. Contact admin.')
                return redirect('Accounts:login_choice')

            role = request.user.profile.role
            if role not in allowed_roles:
                messages.error(request, 'You do not have permission to access this page.')
                return redirect('Accounts:dashboard_redirect')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_required(view_func):
    return role_required(['admin'])(view_func)


def hr_required(view_func):
    return role_required(['hr'])(view_func)


def employee_required(view_func):
    return role_required(['employee'])(view_func)


def admin_or_hr_required(view_func):
    return role_required(['admin', 'hr'])(view_func)