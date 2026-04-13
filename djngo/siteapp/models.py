from django.db import models


class LocationPoint(models.Model):
	lat = models.FloatField()
	lng = models.FloatField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["id"]
		indexes = [
			models.Index(fields=["created_at"]),
		]

	def __str__(self) -> str:
		return f"{self.lat}, {self.lng}"
