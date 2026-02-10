from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Prefetch
from django.contrib.contenttypes.models import ContentType

from ...models import UserWarehouseAccess, Warehouse
from ...models.common import OperationLog


def _parse_warehouse_form_data(post_data):
    selected_warehouse_ids = []
    seen = set()
    for raw_id in post_data.getlist('warehouse_ids'):
        try:
            warehouse_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if warehouse_id in seen:
            continue
        seen.add(warehouse_id)
        selected_warehouse_ids.append(warehouse_id)

    default_warehouse_id = None
    raw_default = (post_data.get('default_warehouse_id') or '').strip()
    if raw_default:
        try:
            default_warehouse_id = int(raw_default)
        except (TypeError, ValueError):
            default_warehouse_id = None

    return selected_warehouse_ids, default_warehouse_id


def _validate_selected_warehouses(selected_warehouse_ids, default_warehouse_id):
    if not selected_warehouse_ids:
        return [], None, []

    warehouse_map = {
        warehouse.id: warehouse
        for warehouse in Warehouse.objects.filter(
            id__in=selected_warehouse_ids,
            is_active=True,
        )
    }
    selected_warehouses = [
        warehouse_map[warehouse_id]
        for warehouse_id in selected_warehouse_ids
        if warehouse_id in warehouse_map
    ]

    errors = []
    if len(selected_warehouses) != len(selected_warehouse_ids):
        errors.append('仓库授权中包含无效或已禁用仓库，请重新选择')

    if default_warehouse_id is None:
        errors.append('已选择仓库时必须指定默认仓库')
    elif default_warehouse_id not in warehouse_map:
        errors.append('默认仓库必须在已授权仓库中')

    return selected_warehouses, default_warehouse_id, errors


def _sync_user_warehouse_accesses(user, selected_warehouses, default_warehouse_id):
    UserWarehouseAccess.objects.filter(user=user).delete()
    if not selected_warehouses:
        return

    UserWarehouseAccess.objects.bulk_create([
        UserWarehouseAccess(
            user=user,
            warehouse=warehouse,
            is_default=(warehouse.id == default_warehouse_id),
            is_active=True,
            permission_bits=UserWarehouseAccess.DEFAULT_PERMISSION_BITS,
        )
        for warehouse in selected_warehouses
    ])


@login_required
@permission_required('auth.view_user', raise_exception=True)
def user_list(request):
    """用户列表视图"""
    # 获取筛选参数
    search_query = request.GET.get('search', '')
    is_active = request.GET.get('is_active', '')
    user_group = request.GET.get('group', '')
    
    active_accesses_prefetch = Prefetch(
        'warehouse_accesses',
        queryset=UserWarehouseAccess.objects.select_related('warehouse').filter(
            is_active=True,
            warehouse__is_active=True,
        ).order_by('-is_default', 'warehouse__name'),
        to_attr='active_warehouse_accesses',
    )

    # 基本查询集
    users = User.objects.prefetch_related('groups', active_accesses_prefetch).all()
    
    # 应用筛选
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query) | 
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query)
        )
    
    if is_active:
        users = users.filter(is_active=(is_active == 'true'))
    
    if user_group:
        users = users.filter(groups__id=user_group)
    
    # 获取用户组
    groups = Group.objects.all()
    
    context = {
        'users': users,
        'groups': groups,
        'search_query': search_query,
        'is_active': is_active,
        'user_group': user_group
    }
    
    return render(request, 'inventory/system/user_list.html', context)


@login_required
@permission_required('auth.add_user', raise_exception=True)
def user_create(request):
    """创建用户视图"""
    groups = Group.objects.all()
    warehouses = Warehouse.objects.filter(is_active=True).order_by('name')
    
    # 确保销售员组存在
    sales_group, created = Group.objects.get_or_create(name='销售员')
    
    # 如果是新创建的组，为其设置相应权限
    if created:
        # 销售相关权限
        content_types = ContentType.objects.filter(
            Q(app_label='inventory', model='sale') |
            Q(app_label='inventory', model='saleitem') |
            Q(app_label='inventory', model='member')
        )
        permissions = Permission.objects.filter(content_type__in=content_types)
        sales_group.permissions.add(*permissions)
        
        # 记录日志
        OperationLog.objects.create(
            operator=request.user,
            operation_type='OTHER',
            details=f'创建销售员用户组并设置权限',
            related_object_id=sales_group.id,
            related_content_type=ContentType.objects.get_for_model(sales_group),
        )
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        password_confirm = request.POST.get('password_confirm')
        email = request.POST.get('email', '')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        is_superuser = request.POST.get('is_superuser') == 'on'
        group_ids = request.POST.getlist('groups')
        selected_warehouse_ids, default_warehouse_id = _parse_warehouse_form_data(request.POST)
        
        # 表单验证
        errors = []
        
        # 用户名验证
        if not username:
            errors.append('用户名不能为空')
        elif User.objects.filter(username=username).exists():
            errors.append('用户名已存在')
        
        # 密码验证
        if not password:
            errors.append('密码不能为空')
        elif len(password) < 8:
            errors.append('密码长度至少为8个字符')
        elif password != password_confirm:
            errors.append('两次输入的密码不一致')

        selected_warehouses, default_warehouse_id, warehouse_errors = _validate_selected_warehouses(
            selected_warehouse_ids,
            default_warehouse_id,
        )
        errors.extend(warehouse_errors)
        
        # 如果有错误，返回错误信息
        if errors:
            messages.error(request, '\n'.join(errors))
            return render(request, 'inventory/system/user_create.html', {
                'groups': groups,
                'warehouses': warehouses,
                'form_data': request.POST,
                'selected_group_ids': [str(group_id) for group_id in group_ids],
                'selected_warehouse_ids': [str(warehouse_id) for warehouse_id in selected_warehouse_ids],
                'default_warehouse_id': str(default_warehouse_id) if default_warehouse_id else '',
            })
        
        with transaction.atomic():
            # 创建用户
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser
            )
            
            # 分配用户组
            if group_ids:
                selected_groups = Group.objects.filter(id__in=group_ids)
                user.groups.add(*selected_groups)

            _sync_user_warehouse_accesses(user, selected_warehouses, default_warehouse_id)
            
            # 记录操作日志
            OperationLog.objects.create(
                operator=request.user,
                operation_type='OTHER',
                details=f'创建用户: {username}（授权仓库 {len(selected_warehouses)} 个）',
                related_object_id=user.id,
                related_content_type=ContentType.objects.get_for_model(user),
            )
        
        messages.success(request, f'用户 {username} 创建成功')
        return redirect('user_list')
    
    return render(request, 'inventory/system/user_create.html', {
        'groups': groups,
        'warehouses': warehouses,
        'selected_group_ids': [],
        'selected_warehouse_ids': [],
        'default_warehouse_id': '',
    })


@login_required
@permission_required('auth.change_user', raise_exception=True)
def user_update(request, pk):
    """更新用户视图"""
    user = get_object_or_404(User, pk=pk)
    groups = Group.objects.all()
    warehouses = Warehouse.objects.filter(is_active=True).order_by('name')
    
    if request.method == 'POST':
        email = request.POST.get('email', '')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        is_superuser = request.POST.get('is_superuser') == 'on'
        group_ids = request.POST.getlist('groups')
        new_password = request.POST.get('new_password', '')
        new_password_confirm = request.POST.get('new_password_confirm', '')
        selected_warehouse_ids, default_warehouse_id = _parse_warehouse_form_data(request.POST)
        
        # 表单验证
        errors = []
        
        # 密码验证
        if new_password:
            if len(new_password) < 8:
                errors.append('密码长度至少为8个字符')
            elif new_password != new_password_confirm:
                errors.append('两次输入的密码不一致')

        selected_warehouses, default_warehouse_id, warehouse_errors = _validate_selected_warehouses(
            selected_warehouse_ids,
            default_warehouse_id,
        )
        errors.extend(warehouse_errors)
        
        # 如果有错误，返回错误信息
        if errors:
            messages.error(request, '\n'.join(errors))
            return render(request, 'inventory/system/user_update.html', {
                'user': user,
                'groups': groups,
                'warehouses': warehouses,
                'form_data': request.POST,
                'selected_group_ids': [str(group_id) for group_id in group_ids],
                'selected_warehouse_ids': [str(warehouse_id) for warehouse_id in selected_warehouse_ids],
                'default_warehouse_id': str(default_warehouse_id) if default_warehouse_id else '',
            })
        
        with transaction.atomic():
            # 更新用户信息
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = is_active
            user.is_staff = is_staff
            user.is_superuser = is_superuser
            
            # 如果提供了新密码，更新密码
            if new_password:
                user.set_password(new_password)
            
            user.save()
            
            # 更新用户组
            user.groups.clear()
            if group_ids:
                selected_groups = Group.objects.filter(id__in=group_ids)
                user.groups.add(*selected_groups)

            _sync_user_warehouse_accesses(user, selected_warehouses, default_warehouse_id)
            
            # 记录操作日志
            OperationLog.objects.create(
                operator=request.user,
                operation_type='OTHER',
                details=f'更新用户: {user.username}（授权仓库 {len(selected_warehouses)} 个）',
                related_object_id=user.id,
                related_content_type=ContentType.objects.get_for_model(user),
            )
        
        messages.success(request, f'用户 {user.username} 更新成功')
        return redirect('user_list')
    active_accesses = list(
        UserWarehouseAccess.objects.filter(
            user=user,
            is_active=True,
            warehouse__is_active=True,
        ).select_related('warehouse').order_by('-is_default', 'warehouse__name')
    )
    selected_warehouse_ids = [str(access.warehouse_id) for access in active_accesses]
    default_access = next((access for access in active_accesses if access.is_default), None)
    default_warehouse_id = str(default_access.warehouse_id) if default_access else (
        selected_warehouse_ids[0] if selected_warehouse_ids else ''
    )

    return render(request, 'inventory/system/user_update.html', {
        'user': user,
        'groups': groups,
        'warehouses': warehouses,
        'selected_group_ids': [str(group_id) for group_id in user.groups.values_list('id', flat=True)],
        'selected_warehouse_ids': selected_warehouse_ids,
        'default_warehouse_id': default_warehouse_id,
    })


@login_required
@permission_required('auth.delete_user', raise_exception=True)
def user_delete(request, pk):
    """删除用户视图"""
    user = get_object_or_404(User, pk=pk)
    
    # 防止删除自己
    if user == request.user:
        messages.error(request, '不能删除当前登录的用户')
        return redirect('user_list')
    
    if request.method == 'POST':
        username = user.username
        user.delete()
        
        # 记录操作日志
        OperationLog.objects.create(
            operator=request.user,
            operation_type='OTHER',
            details=f'删除用户: {username}',
            related_object_id=pk,
            related_content_type=ContentType.objects.get_for_model(User),
        )
        
        messages.success(request, f'用户 {username} 已删除')
        return redirect('user_list')
    
    return render(request, 'inventory/system/user_delete.html', {
        'user': user
    })


@login_required
@permission_required('auth.view_user', raise_exception=True)
def user_detail(request, pk):
    """用户详情视图"""
    user = get_object_or_404(User, pk=pk)
    
    # 获取用户最近的操作日志
    logs = OperationLog.objects.filter(operator=user).order_by('-timestamp')[:20]
    warehouse_accesses = UserWarehouseAccess.objects.filter(
        user=user,
        is_active=True,
        warehouse__is_active=True,
    ).select_related('warehouse').order_by('-is_default', 'warehouse__name')
    
    return render(request, 'inventory/system/user_detail.html', {
        'user': user,
        'logs': logs,
        'warehouse_accesses': warehouse_accesses,
    })
