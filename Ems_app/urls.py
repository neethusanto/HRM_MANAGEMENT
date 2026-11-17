from django.urls import path
from . import views

app_name = 'Ems_app'

urlpatterns = [
    # Dashboards
    path('admin/', views.admin_dashboard, name='admin_dashboard'),
    path('hr-dashboard/', views.hr_dashboard, name='hr_dashboard'),
    path('employee/', views.employee_dashboard, name='employee_dashboard'),

    # Departments
    path('departments/', views.department_list, name='department_list'),
    path('departments/create/', views.department_create, name='department_create'),
    path('departments/edit/<int:pk>/', views.department_edit, name='department_edit'),
    path('departments/delete/<int:pk>/', views.department_delete, name='department_delete'),

    # HR
    path('hr/', views.hr_list, name='hr_list'),
    path('hr/create/', views.hr_create, name='hr_create'),
    path('hr/edit/<int:pk>/', views.hr_edit, name='hr_edit'),
    path('hr/delete/<int:pk>/', views.hr_delete, name='hr_delete'),

    # Employees
    path('employees/', views.employee_list, name='employee_list'),
    path('employees/create/', views.employee_create, name='employee_create'),
    path('employees/bulk-upload/', views.employee_bulk_upload, name='employee_bulk_upload'),
    path('employees/edit/<int:pk>/', views.employee_edit, name='employee_edit'),
    path('employees/delete/<int:pk>/', views.employee_delete, name='employee_delete'),

    # Profile
    path('profile/', views.profile, name='profile'),

    # Attendance
    path('attendance/', views.attendance_list, name='attendance_list'),
    path('attendance/add/', views.attendance_create, name='attendance_create'),
    path('my-attendance/', views.employee_attendance, name='employee_attendance'),
    path('my-attendance/calendar/', views.employee_attendance_calendar, name='employee_attendance_calendar'),
    path('attendance/edit/<int:pk>/', views.attendance_edit, name='attendance_edit'),
    path('attendance/<int:pk>/verify/', views.attendance_verify, name='attendance_verify'),
    path('attendance/delete/<int:pk>/', views.attendance_delete, name='attendance_delete'),
    path('attendance/employee/<int:pk>/', views.employee_monthly_attendance, name='employee_monthly_attendance'),
    path('attendance/employee/<int:pk>/calendar/', views.employee_attendance_calendar, name='employee_attendance_employee_calendar'),
    path('attendance/bulk-upload/', views.attendance_bulk_upload, name='attendance_bulk_upload'),
    path('attendance/template/', views.attendance_download_template, name='attendance_download_template'),

    # Employee self check-in/out
    path('check-in/', views.check_in, name='check_in'),
    path('check-out/', views.check_out, name='check_out'),

    # Punch-machine API
    path('api/device/punch/', views.device_punch, name='device_punch'),

    # Leave
    path('leaves/', views.leave_list, name='leave_list'),
    path('leaves/apply/', views.leave_apply, name='leave_apply'),
    path('leaves/<int:pk>/', views.leave_detail, name='leave_detail'),
    path('leaves/<int:pk>/approve/', views.leave_approve, name='leave_approve'),
    path('leaves/<int:pk>/reject/', views.leave_reject, name='leave_reject'),

   # Salary
    path('salaries/', views.salary_list, name='salary_list'),
    path('salaries/create/', views.salary_create, name='salary_create'),
    path('salaries/edit/<int:pk>/', views.salary_edit, name='salary_edit'),
    path('salaries/delete/<int:pk>/', views.salary_delete, name='salary_delete'),
    path('salaries/<int:pk>/request-payslip/', views.payslip_request_create, name='payslip_request_create'),
    path('payslip-requests/', views.payslip_request_list, name='payslip_request_list'),
    path('payslip-requests/<int:pk>/<str:decision>/', views.payslip_request_review, name='payslip_request_review'),
    path('salaries/<int:pk>/payslip/', views.salary_payslip, name='salary_payslip'),
    path('salaries/<int:pk>/payslip/pdf/', views.salary_payslip_pdf, name='salary_payslip_pdf'),
    # Reports
    path('reports/', views.reports, name='reports'),
    path('reports/download-monthly/', views.reports_download_monthly, name='reports_download_monthly'),
]
