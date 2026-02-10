from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Prefetch
from django.contrib.contenttypes.models import ContentType

from ...models import UserWarehouseAccess, Warehouse
from ...models.common import OperationLog
from ...permissions.decorators import superuser_required

WAREHOUSE_ROLE_TEMPLATES = {
    'warehouse_manager': [
        'view',
        'sale',
        'stock_in',
        'stock_out',
        'inventory_check',
        'stock_adjust',
        'product_manage',
        'report_view',
    ],
    'cashier': [
        'view',
        'sale',
    ],
}

CORE_USER_GROUP_NAMES = (
    '超级管理员',
    '分仓管理员',
    '销售员',
)


def _ensure_core_user_groups():
    """Ensure core role groups exist and return (group_map, created_map)."""
    group_map = {}
    created_map = {}
    for group_name in CORE_USER_GROUP_NAMES:
        group, created = Group.objects.get_or_create(name=group_name)
        group_map[group_name] = group
        created_map[group_name] = created
    return group_map, created_map


def _normalize_group_ids(group_ids, is_superuser, superuser_group):
    normalized_group_ids = []
    seen = set()
    for raw_group_id in group_ids:
        normalized_group_id = str(raw_group_id or '').strip()
        if not normalized_group_id or normalized_group_id in seen:
            continue
        seen.add(normalized_group_id)
        normalized_group_ids.append(normalized_group_id)

    superuser_group_id = str(superuser_group.id) if superuser_group else None
    if superuser_group_id:
        if is_superuser and superuser_group_id not in normalized_group_ids:
            normalized_group_ids.append(superuser_group_id)
        if not is_superuser:
            normalized_group_ids = [
                group_id for group_id in normalized_group_ids
                if group_id != superuser_group_id
            ]

    return normalized_group_ids


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


def _parse_warehouse_permission_codes(post_data, selected_warehouse_ids):
    default_codes = UserWarehouseAccess.codes_from_bits(UserWarehouseAccess.DEFAULT_PERMISSION_BITS)
    valid_codes = set(UserWarehouseAccess.PERMISSION_CODE_TO_BIT.keys())
    permission_codes = {}

    for warehouse_id in selected_warehouse_ids:
        field_name = f'warehouse_permissions_{warehouse_id}'
        codes = []
        seen = set()
        for raw_code in post_data.getlist(field_name):
            normalized_code = str(raw_code or '').strip().lower()
            if normalized_code not in valid_codes or normalized_code in seen:
                continue
            seen.add(normalized_code)
            codes.append(normalized_code)
        permission_codes[str(warehouse_id)] = codes or list(default_codes)

    return permission_codes


def _build_permission_bits_by_warehouse(permission_codes):
    permission_bits_by_warehouse = {}
    for warehouse_id, codes in permission_codes.items():
        try:
            normalized_warehouse_id = int(warehouse_id)
        except (TypeError, ValueError):
            continue
        permission_bits_by_warehouse[normalized_warehouse_id] = UserWarehouseAccess.ensure_permission_bits(
            UserWarehouseAccess.bits_for_codes(codes)
        )
    return permission_bits_by_warehouse


def _build_permission_codes_from_accesses(accesses):
    permission_codes = {}
    for access in accesses:
        codes = UserWarehouseAccess.codes_from_bits(access.permission_bits)
        permission_codes[str(access.warehouse_id)] = codes or UserWarehouseAccess.codes_from_bits(
            UserWarehouseAccess.DEFAULT_PERMISSION_BITS
        )
    return permission_codes


def _prepare_warehouse_form_context(warehouses, selected_warehouse_ids, permission_codes=None):
    selected_set = {str(warehouse_id) for warehouse_id in selected_warehouse_ids}
    default_codes = UserWarehouseAccess.codes_from_bits(UserWarehouseAccess.DEFAULT_PERMISSION_BITS)

    for warehouse in warehouses:
        warehouse_key = str(warehouse.id)
        warehouse.selected_for_user = warehouse_key in selected_set
        selected_codes = None
        if permission_codes:
            selected_codes = permission_codes.get(warehouse_key)
        if selected_codes is None:
            selected_codes = list(default_codes) if warehouse.selected_for_user else []
        warehouse.selected_permission_codes = list(selected_codes)

    return warehouses


def _decorate_access_permissions(accesses):
    for access in accesses:
        labels = UserWarehouseAccess.labels_from_bits(access.permission_bits)
        access.permission_labels = labels
        if labels:
            visible_labels = labels[:2]
            summary = '、'.join(visible_labels)
            if len(labels) > 2:
                summary = f'{summary} +{len(labels) - 2}'
            access.permission_summary = summary
        else:
            access.permission_summary = '未配置权限'


def _sync_user_warehouse_accesses(
    user,
    selected_warehouses,
    default_warehouse_id,
    permission_bits_by_warehouse=None,
):
    UserWarehouseAccess.objects.filter(user=user).delete()
    if not selected_warehouses:
        return

    permission_bits_by_warehouse = permission_bits_by_warehouse or {}
    UserWarehouseAccess.objects.bulk_create([
        UserWarehouseAccess(
            user=user,
            warehouse=warehouse,
            is_default=(warehouse.id == default_warehouse_id),
            is_active=True,
            permission_bits=UserWarehouseAccess.ensure_permission_bits(
                permission_bits_by_warehouse.get(warehouse.id)
            ),
        )
        for warehouse in selected_warehouses
    ])


@login_required
@superuser_required
def user_list(request):
    """用户列表视图"""
    _ensure_core_user_groups()

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
    
    users = list(users)
    for user in users:
        _decorate_access_permissions(getattr(user, 'active_warehouse_accesses', []))

    # 获取用户组
    groups = Group.objects.order_by('name')
    
    context = {
        'users': users,
        'groups': groups,
        'search_query': search_query,
        'is_active': is_active,
        'user_group': user_group
    }
    
    return render(request, 'inventory/system/user_list.html', context)


@login_required
@superuser_required
def user_create(request):
    """创建用户视图"""
    core_groups, created_groups = _ensure_core_user_groups()
    groups = Group.objects.order_by('name')
    warehouses = list(Warehouse.objects.filter(is_active=True).order_by('name'))
    permission_choices = UserWarehouseAccess.get_permission_catalog()
    
    sales_group = core_groups.get('销售员')
    
    # 如果是新创建的组，为其设置相应权限
    if created_groups.get('销售员'):
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
        group_ids = _normalize_group_ids(
            request.POST.getlist('groups'),
            is_superuser=is_superuser,
            superuser_group=core_groups.get('超级管理员'),
        )
        selected_warehouse_ids, default_warehouse_id = _parse_warehouse_form_data(request.POST)
        permission_codes = _parse_warehouse_permission_codes(request.POST, selected_warehouse_ids)
        permission_bits_by_warehouse = _build_permission_bits_by_warehouse(permission_codes)
        
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
                'warehouses': _prepare_warehouse_form_context(
                    warehouses,
                    selected_warehouse_ids,
                    permission_codes,
                ),
                'form_data': request.POST,
                'selected_group_ids': [str(group_id) for group_id in group_ids],
                'selected_warehouse_ids': [str(warehouse_id) for warehouse_id in selected_warehouse_ids],
                'default_warehouse_id': str(default_warehouse_id) if default_warehouse_id else '',
                'warehouse_permission_choices': permission_choices,
                'warehouse_role_templates': WAREHOUSE_ROLE_TEMPLATES,
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

            _sync_user_warehouse_accesses(
                user,
                selected_warehouses,
                default_warehouse_id,
                permission_bits_by_warehouse=permission_bits_by_warehouse,
            )
            
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
        'warehouses': _prepare_warehouse_form_context(
            warehouses,
            selected_warehouse_ids=[],
            permission_codes={},
        ),
        'selected_group_ids': [],
        'selected_warehouse_ids': [],
        'default_warehouse_id': '',
        'warehouse_permission_choices': permission_choices,
        'warehouse_role_templates': WAREHOUSE_ROLE_TEMPLATES,
    })


@login_required
@superuser_required
def user_update(request, pk):
    """更新用户视图"""
    core_groups, _ = _ensure_core_user_groups()
    user = get_object_or_404(User, pk=pk)
    groups = Group.objects.order_by('name')
    warehouses = list(Warehouse.objects.filter(is_active=True).order_by('name'))
    permission_choices = UserWarehouseAccess.get_permission_catalog()
    
    if request.method == 'POST':
        email = request.POST.get('email', '')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        is_superuser = request.POST.get('is_superuser') == 'on'
        group_ids = _normalize_group_ids(
            request.POST.getlist('groups'),
            is_superuser=is_superuser,
            superuser_group=core_groups.get('超级管理员'),
        )
        new_password = request.POST.get('new_password', '')
        new_password_confirm = request.POST.get('new_password_confirm', '')
        selected_warehouse_ids, default_warehouse_id = _parse_warehouse_form_data(request.POST)
        permission_codes = _parse_warehouse_permission_codes(request.POST, selected_warehouse_ids)
        permission_bits_by_warehouse = _build_permission_bits_by_warehouse(permission_codes)
        
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
                'warehouses': _prepare_warehouse_form_context(
                    warehouses,
                    selected_warehouse_ids,
                    permission_codes,
                ),
                'form_data': request.POST,
                'selected_group_ids': [str(group_id) for group_id in group_ids],
                'selected_warehouse_ids': [str(warehouse_id) for warehouse_id in selected_warehouse_ids],
                'default_warehouse_id': str(default_warehouse_id) if default_warehouse_id else '',
                'warehouse_permission_choices': permission_choices,
                'warehouse_role_templates': WAREHOUSE_ROLE_TEMPLATES,
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

            _sync_user_warehouse_accesses(
                user,
                selected_warehouses,
                default_warehouse_id,
                permission_bits_by_warehouse=permission_bits_by_warehouse,
            )
            
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
    permission_codes = _build_permission_codes_from_accesses(active_accesses)
    default_access = next((access for access in active_accesses if access.is_default), None)
    default_warehouse_id = str(default_access.warehouse_id) if default_access else (
        selected_warehouse_ids[0] if selected_warehouse_ids else ''
    )

    return render(request, 'inventory/system/user_update.html', {
        'user': user,
        'groups': groups,
        'warehouses': _prepare_warehouse_form_context(
            warehouses,
            selected_warehouse_ids,
            permission_codes,
        ),
        'selected_group_ids': [str(group_id) for group_id in user.groups.values_list('id', flat=True)],
        'selected_warehouse_ids': selected_warehouse_ids,
        'default_warehouse_id': default_warehouse_id,
        'warehouse_permission_choices': permission_choices,
        'warehouse_role_templates': WAREHOUSE_ROLE_TEMPLATES,
    })


@login_required
@superuser_required
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
@superuser_required
def user_detail(request, pk):
    """用户详情视图"""
    user = get_object_or_404(User, pk=pk)
    
    # 获取用户最近的操作日志
    logs = OperationLog.objects.filter(operator=user).order_by('-timestamp')[:20]
    warehouse_accesses = list(UserWarehouseAccess.objects.filter(
        user=user,
        is_active=True,
        warehouse__is_active=True,
    ).select_related('warehouse').order_by('-is_default', 'warehouse__name'))
    _decorate_access_permissions(warehouse_accesses)
    
    return render(request, 'inventory/system/user_detail.html', {
        'user': user,
        'logs': logs,
        'warehouse_accesses': warehouse_accesses,
    })
