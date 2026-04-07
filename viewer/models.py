from django.db import models


class SharedStudy(models.Model):
    """
    Persists the instance-ID selection for a cache_id so that expired studies
    can be automatically re-downloaded when a shared link is opened.
    """
    cache_id     = models.CharField(max_length=200, unique=True, db_index=True)
    study_id     = models.CharField(max_length=200)
    instance_ids = models.JSONField()          # list of Orthanc instance ID strings
    created_at   = models.DateTimeField(auto_now_add=True)
    last_opened  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.study_id} / {self.cache_id}"
