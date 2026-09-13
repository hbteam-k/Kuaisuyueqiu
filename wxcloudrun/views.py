import json
import logging
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from wxcloudrun.models import Counters, RoomRecord


logger = logging.getLogger('log')


def index(request, _):
    """
    获取主页

     `` request `` 请求对象
    """

    return render(request, 'index.html')


def counter(request, _):
    """
    获取当前计数

     `` request `` 请求对象
    """

    rsp = JsonResponse({'code': 0, 'errorMsg': ''}, json_dumps_params={'ensure_ascii': False})
    if request.method == 'GET' or request.method == 'get':
        rsp = get_count()
    elif request.method == 'POST' or request.method == 'post':
        rsp = update_count(request)
    else:
        rsp = JsonResponse({'code': -1, 'errorMsg': '请求方式错误'},
                            json_dumps_params={'ensure_ascii': False})
    logger.info('response result: {}'.format(rsp.content.decode('utf-8')))
    return rsp


def get_count():
    """
    获取当前计数
    """

    try:
        data = Counters.objects.get(id=1)
    except Counters.DoesNotExist:
        return JsonResponse({'code': 0, 'data': 0},
                    json_dumps_params={'ensure_ascii': False})
    return JsonResponse({'code': 0, 'data': data.count},
                        json_dumps_params={'ensure_ascii': False})


def update_count(request):
    """
    更新计数，自增或者清零

    `` request `` 请求对象
    """

    logger.info('update_count req: {}'.format(request.body))

    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)

    if 'action' not in body:
        return JsonResponse({'code': -1, 'errorMsg': '缺少action参数'},
                            json_dumps_params={'ensure_ascii': False})

    if body['action'] == 'inc':
        try:
            data = Counters.objects.get(id=1)
        except Counters.DoesNotExist:
            data = Counters()
        data.id = 1
        data.count += 1
        data.save()
        return JsonResponse({'code': 0, "data": data.count},
                    json_dumps_params={'ensure_ascii': False})
    elif body['action'] == 'clear':
        try:
            data = Counters.objects.get(id=1)
            data.delete()
        except Counters.DoesNotExist:
            logger.info('record not exist')
        return JsonResponse({'code': 0, 'data': 0},
                    json_dumps_params={'ensure_ascii': False})
    else:
        return JsonResponse({'code': -1, 'errorMsg': 'action参数错误'},
                    json_dumps_params={'ensure_ascii': False})


def _request_json(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (TypeError, ValueError, UnicodeDecodeError):
        return {}


def _record_room(record):
    try:
        return json.loads(record.payload)
    except (TypeError, ValueError):
        return {}


def _delete_expired_rooms():
    """按产品规则清理结束超过两天的服务器记录。"""
    now = datetime.utcnow()
    for record in RoomRecord.objects.all().only('id', 'payload'):
        room = _record_room(record)
        end_at = room.get('endAt')
        if not end_at:
            continue
        try:
            end = datetime.fromisoformat(end_at.replace('Z', '+00:00')).replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if now > end + timedelta(days=2):
            record.delete()


def _ensure_room_table():
    """模板首次部署没有迁移命令，首次请求时确保新表存在。"""
    table_name = RoomRecord._meta.db_table
    if table_name in connection.introspection.table_names():
        return
    try:
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(RoomRecord)
    except Exception:
        # 并发首次请求可能由另一个实例先创建，后续查询会验证最终状态。
        if table_name not in connection.introspection.table_names():
            raise


def rooms(request, *args):
    """服务器权威场地接口：list/create/join/profile。"""
    if request.method != 'POST':
        return JsonResponse({'code': -1, 'errorMsg': '仅支持 POST'}, status=405)

    _ensure_room_table()

    body = _request_json(request)
    action = body.get('action')

    if action == 'list':
        _delete_expired_rooms()
        result = [_record_room(record)
                  for record in RoomRecord.objects.order_by('-updated_at')]
        return JsonResponse({'code': 0, 'data': {'rooms': result}},
                            json_dumps_params={'ensure_ascii': False})

    if action == 'create':
        room = body.get('room') or {}
        room_id = str(room.get('id') or '')
        if not room_id:
            return JsonResponse({'code': -1, 'errorMsg': '缺少场地 ID'}, status=400)
        RoomRecord.objects.update_or_create(
            room_id=room_id,
            defaults={
                'payload': json.dumps(room, ensure_ascii=False),
                'owner_user_id': str(room.get('ownerUserId') or ''),
            },
        )
        return JsonResponse({'code': 0, 'data': {'room': room}},
                            json_dumps_params={'ensure_ascii': False})

    if action == 'join':
        incoming = body.get('room') or {}
        room_id = str(incoming.get('id') or '')
        if not room_id:
            return JsonResponse({'code': -1, 'errorMsg': '缺少场地 ID'}, status=400)

        with transaction.atomic():
            try:
                record = RoomRecord.objects.select_for_update().get(room_id=room_id)
            except RoomRecord.DoesNotExist:
                return JsonResponse({'code': -1, 'errorMsg': '场地不存在'}, status=404)

            room = _record_room(record)
            current_members = room.get('members') or []
            incoming_members = incoming.get('members') or []
            if len(current_members) < len(incoming_members):
                current_members.extend([None] * (len(incoming_members) - len(current_members)))

            for index, member in enumerate(incoming_members):
                if member and not current_members[index]:
                    current_members[index] = member

            room['members'] = current_members
            record.payload = json.dumps(room, ensure_ascii=False)
            record.save(update_fields=['payload', 'updated_at'])

        return JsonResponse({'code': 0, 'data': {'room': room}},
                            json_dumps_params={'ensure_ascii': False})

    if action == 'profile':
        profile = body.get('profile') or {}
        user_id = str(profile.get('userId') or '')
        wechat_name = str(profile.get('wechatName') or profile.get('nickName') or '')

        for record in RoomRecord.objects.all():
            room = _record_room(record)
            changed = False
            if (str(room.get('ownerUserId') or '') == user_id or
                    (wechat_name and room.get('ownerWechatName') == wechat_name)):
                room['owner'] = profile.get('nickName') or room.get('owner')
                room['ownerNote'] = profile.get('note') or ''
                changed = True

            for member in room.get('members') or []:
                if not member:
                    continue
                if (str(member.get('userId') or '') == user_id or
                        (wechat_name and member.get('wechatName') == wechat_name)):
                    member.update({
                        'name': profile.get('nickName') or member.get('name'),
                        'wechatName': wechat_name,
                        'avatarUrl': profile.get('avatarUrl') or member.get('avatarUrl', ''),
                        'note': profile.get('note') or '',
                    })
                    changed = True

            if changed:
                record.payload = json.dumps(room, ensure_ascii=False)
                record.save(update_fields=['payload', 'updated_at'])

        return JsonResponse({'code': 0, 'data': {'profile': profile}},
                            json_dumps_params={'ensure_ascii': False})

    return JsonResponse({'code': -1, 'errorMsg': '未知 action'}, status=400)
