from django.urls import path
from . import views, views_crud

urlpatterns = [
    path("", views.home, name="home"),
    path("api/parse/", views.parse_entry, name="parse_entry"),
    path("api/save/", views.save_entry, name="save_entry"),
    path("expenses/", views_crud.expense_list, name="expense_list"),
    path("expenses/<int:pk>/", views_crud.expense_detail, name="expense_detail"),
    path("expenses/<int:pk>/edit/", views_crud.expense_edit, name="expense_edit"),
    path("expenses/<int:pk>/delete/", views_crud.expense_delete, name="expense_delete"),
    path("tasks/", views_crud.task_list, name="task_list"),
    path("tasks/<int:pk>/", views_crud.task_detail, name="task_detail"),
    path("tasks/<int:pk>/edit/", views_crud.task_edit, name="task_edit"),
    path("tasks/<int:pk>/delete/", views_crud.task_delete, name="task_delete"),
    path("notes/", views_crud.note_list, name="note_list"),
    path("notes/<int:pk>/", views_crud.note_detail, name="note_detail"),
    path("notes/<int:pk>/edit/", views_crud.note_edit, name="note_edit"),
    path("notes/<int:pk>/delete/", views_crud.note_delete, name="note_delete"),
]
