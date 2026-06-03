from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('audit-log/', views.AuditLogView.as_view(), name='audit_log'),
    path('users/creer/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
    path('users/<int:pk>/modifier/', views.UserEditView.as_view(), name='user_edit'),
    path('users/<int:pk>/supprimer/', views.UserDeleteView.as_view(), name='user_delete'),
]
