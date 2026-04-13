from django.contrib import admin

from .models import LocationPoint


@admin.register(LocationPoint)
class LocationPointAdmin(admin.ModelAdmin):
	list_display = ("id", "lat", "lng", "created_at")
	list_filter = ("created_at",)
	search_fields = ("lat", "lng")
