from django.urls import path

from . import views

app_name = "guides"

urlpatterns = [
    path("", views.GuideListView.as_view(), name="list"),
    path("ny/", views.GuideCreateView.as_view(), name="create"),
    path("<int:pk>/", views.GuideDetailView.as_view(), name="detail"),
    path("<int:pk>/rediger/", views.GuideUpdateView.as_view(), name="update"),
    path("<int:pk>/slet/", views.GuideDeleteView.as_view(), name="delete"),
    path("<int:pk>/forside/", views.serve_cover, name="cover"),
    path("<int:guide_pk>/afsnit/ny/", views.SectionEditView.as_view(), name="section_create"),
    path("afsnit/<int:pk>/rediger/", views.SectionEditView.as_view(), name="section_update"),
    path("afsnit/<int:pk>/slet/", views.SectionDeleteView.as_view(), name="section_delete"),
    path("afsnit/<int:pk>/flyt/", views.SectionMoveView.as_view(), name="section_move"),
    path("billede/<int:pk>/", views.serve_image, name="image"),
]
