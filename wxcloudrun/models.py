from datetime import datetime

from django.db import models


# Create your models here.
class Counters(models.Model):
    id = models.AutoField(primary_key=True)
    count = models.IntegerField(default=0)
    createdAt = models.DateTimeField(default=datetime.now)
    updatedAt = models.DateTimeField(default=datetime.now)

    def __str__(self):
        return str(self.count)

    class Meta:
        db_table = 'Counters'  # 数据库表名


class RoomRecord(models.Model):
    """服务器唯一保存的场地记录。payload 保持小程序当前数据结构。"""

    room_id = models.CharField(max_length=128, unique=True)
    payload = models.TextField()
    owner_user_id = models.CharField(max_length=128, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'room_records'
