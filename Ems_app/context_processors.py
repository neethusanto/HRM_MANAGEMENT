def user_role(request):
    role = 'employee'
    if request.user.is_authenticated and hasattr(request.user, 'profile'):
        role = request.user.profile.role
    return {'user_role': role}