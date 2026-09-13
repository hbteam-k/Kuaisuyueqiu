import json
import logging
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from wxcloudrun.models import CancellationNotice, Counters, RoomRecord, UserProfile


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
    """按产品规则清理结束超过三天的服务器记录。"""
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
        if now > end + timedelta(days=3):
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


def _ensure_profile_table():
    table_name = UserProfile._meta.db_table
    if table_name in connection.introspection.table_names():
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor(), table_name
            )
        }
        if 'nick_name' not in columns:
            with connection.schema_editor() as schema_editor:
                schema_editor.add_field(UserProfile, UserProfile._meta.get_field('nick_name'))
        return
    try:
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(UserProfile)
    except Exception:
        if table_name not in connection.introspection.table_names():
            raise


def _ensure_notice_table():
    table_name = CancellationNotice._meta.db_table
    if table_name in connection.introspection.table_names():
        return
    try:
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(CancellationNotice)
    except Exception:
        if table_name not in connection.introspection.table_names():
            raise


def _profile_payload_for_actor(request):
    key = _actor_key(request)
    if not key:
        return {}
    try:
        return json.loads(UserProfile.objects.get(actor_key=key).payload) or {}
    except (UserProfile.DoesNotExist, TypeError, ValueError):
        return {}


def _actor_key(request, profile=None):
    openid = request.META.get('HTTP_X_WX_OPENID') or request.META.get('HTTP_X_WX_FROM_OPENID')
    if openid:
        return str(openid)
    profile = profile or {}
    return str(profile.get('userId') or profile.get('wechatName') or profile.get('nickName') or '')


def rooms(request, *args):
    """服务器权威场地接口：list/create/join/profile。"""
    if request.method != 'POST':
        return JsonResponse({'code': -1, 'errorMsg': '仅支持 POST'}, status=405)

    body = _request_json(request)
    action = body.get('action')

    _ensure_room_table()
    if action in ('profile', 'profile_get'):
        _ensure_profile_table()
    if action in ('list', 'delete'):
        _ensure_profile_table()
        _ensure_notice_table()

    if action == 'list':
        _delete_expired_rooms()
        result = [_record_room(record)
                  for record in RoomRecord.objects.order_by('-updated_at')]
        profile = _profile_payload_for_actor(request)
        notices = []
        user_id = str(profile.get('userId') or '')
        if user_id:
            notices = [json.loads(item.payload) for item in
                       CancellationNotice.objects.filter(recipient_user_id=user_id)
                       .order_by('created_at')]
            CancellationNotice.objects.filter(recipient_user_id=user_id).delete()
        return JsonResponse({'code': 0, 'data': {'rooms': result, 'notices': notices}},
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

    if action == 'delete':
        incoming = body.get('room') or {}
        room_id = str(incoming.get('id') or '')
        profile = body.get('profile') or {}
        if not room_id:
            return JsonResponse({'code': -1, 'errorMsg': '缺少场地 ID'}, status=400)
        actor_key = _actor_key(request, profile)
        stored_profile = _profile_payload_for_actor(request)
        actor_user_id = str(stored_profile.get('userId') or profile.get('userId') or '')
        try:
            record = RoomRecord.objects.get(room_id=room_id)
        except RoomRecord.DoesNotExist:
            return JsonResponse({'code': -1, 'errorMsg': '场地不存在'}, status=404)
        room = _record_room(record)
        owner_match = (actor_user_id and
                       (str(room.get('ownerUserId') or '') == actor_user_id or
                        str(record.owner_user_id or '') == actor_user_id))
        if not owner_match and actor_key and stored_profile:
            owner_match = room.get('ownerWechatName') == stored_profile.get('wechatName')
        if not owner_match:
            return JsonResponse({'code': -1, 'errorMsg': '没有删除权限'}, status=403)
        try:
            start = datetime.fromisoformat(str(room.get('startAt')).replace('Z', '+00:00')).replace(tzinfo=None)
        except (TypeError, ValueError):
            return JsonResponse({'code': -1, 'errorMsg': '场地时间无效'}, status=400)
        if datetime.utcnow() >= start:
            return JsonResponse({'code': -1, 'errorMsg': '已开始的对局不能删除'}, status=400)
        members = [member for member in (room.get('members') or []) if member]
        capacity = max(2, int(room.get('capacity') or 2))
        if len(members) > capacity:
            return JsonResponse({'code': -1, 'errorMsg': '场地状态已变化，请刷新后重试'}, status=400)
        record.delete()
        for member in members:
            recipient = str(member.get('userId') or '')
            if not recipient or recipient == actor_user_id:
                continue
            notice_id = f'{room_id}-{recipient}'
            CancellationNotice.objects.update_or_create(
                notice_id=notice_id,
                defaults={
                    'recipient_user_id': recipient,
                    'room_id': room_id,
                    'payload': json.dumps({
                        'roomId': room_id,
                        'title': room.get('title') or '拼场对局',
                        'message': '你参加的对局已被发起人取消',
                    }, ensure_ascii=False),
                },
            )
        return JsonResponse({'code': 0, 'data': {'room': room}},
                            json_dumps_params={'ensure_ascii': False})

    if action == 'profile_get':
        key = _actor_key(request)
        if not key:
            return JsonResponse({'code': 0, 'data': {'profile': None}},
                                json_dumps_params={'ensure_ascii': False})
        try:
            profile = json.loads(UserProfile.objects.get(actor_key=key).payload)
        except UserProfile.DoesNotExist:
            profile = None
        return JsonResponse({'code': 0, 'data': {'profile': profile}},
                            json_dumps_params={'ensure_ascii': False})

    if action == 'profile':
        profile = body.get('profile') or {}
        user_id = str(profile.get('userId') or '')
        nick_name = str(profile.get('nickName') or '').strip()
        wechat_name = str(profile.get('wechatName') or nick_name).strip()
        if not nick_name:
            return JsonResponse({'code': -1, 'errorMsg': '昵称不能为空'}, status=400,
                                json_dumps_params={'ensure_ascii': False})

        key = _actor_key(request, profile)
        if not key:
            return JsonResponse({'code': -1, 'errorMsg': '无法识别当前用户'}, status=400,
                                json_dumps_params={'ensure_ascii': False})

        profile['nickName'] = nick_name
        profile['wechatName'] = wechat_name
        try:
            with transaction.atomic():
                duplicate = False
                for existing in (UserProfile.objects
                                  .exclude(actor_key=key)
                                  .only('actor_key', 'nick_name', 'payload')):
                    existing_name = str(existing.nick_name or '').strip()
                    if not existing_name:
                        try:
                            existing_name = str(
                                (json.loads(existing.payload) or {}).get('nickName') or ''
                            ).strip()
                        except (TypeError, ValueError):
                            existing_name = ''
                    if existing_name == nick_name:
                        duplicate = True
                        break
                if duplicate:
                    return JsonResponse(
                        {'code': -2, 'errorMsg': '昵称已被使用，请换一个昵称'},
                        json_dumps_params={'ensure_ascii': False},
                    )
                UserProfile.objects.update_or_create(
                    actor_key=key,
                    defaults={
                        'nick_name': nick_name,
                        'payload': json.dumps(profile, ensure_ascii=False),
                    },
                )
        except Exception as error:
            # 唯一索引是并发场景下的最终保护，避免两个请求同时通过查询。
            if 'Duplicate entry' in str(error) or 'unique constraint' in str(error).lower():
                return JsonResponse(
                    {'code': -2, 'errorMsg': '昵称已被使用，请换一个昵称'},
                    json_dumps_params={'ensure_ascii': False},
                )
            raise

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
                        'wechatId': profile.get('wechatId') or member.get('wechatId', ''),
                        'phone': profile.get('phone') or member.get('phone', ''),
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
