# from django.urls import path
# from . import views

# app_name = 'Accounts'

# urlpatterns = [
#     path('', views.login_choice, name='login_choice'),
#     path('admin-login/', views.admin_login_view, name='admin_login'),
#     path('hr-login/', views.hr_login_view, name='hr_login'),
#     path('employee-login/', views.employee_login_view, name='employee_login'),
#     path('logout/', views.logout_view, name='logout'),
#     path('dashboard-redirect/', views.dashboard_redirect, name='dashboard_redirect'),
# ]

from django.urls import path
from . import views

app_name = 'Accounts'

urlpatterns = [
    # Unified single login
    path('', views.unified_login, name='login_choice'),
    path('login/', views.unified_login, name='login'),

    # Legacy role URLs → redirect to unified login (kept for old bookmarks)
    path('admin-login/', views.unified_login, name='admin_login'),
    path('hr-login/', views.unified_login, name='hr_login'),
    path('employee-login/', views.unified_login, name='employee_login'),

    path('logout/', views.logout_view, name='logout'),
    path('dashboard-redirect/', views.dashboard_redirect, name='dashboard_redirect'),
]