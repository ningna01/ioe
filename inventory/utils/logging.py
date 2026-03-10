"""
Logging utilities for the inventory system.
"""
import functools
import json
import logging
import traceback
from typing import Any

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

logger = logging.getLogger(__name__)

LOGENTRY_ACTION_ADDITION = 1
LOGENTRY_ACTION_CHANGE = 2
LOGENTRY_ACTION_DELETION = 3

LEGACY_LOG_ACTION_FLAGS = {
    'BACKUP': LOGENTRY_ACTION_CHANGE,
    'RESTORE': LOGENTRY_ACTION_CHANGE,
    'DOWNLOAD': LOGENTRY_ACTION_CHANGE,
    'ERROR': LOGENTRY_ACTION_CHANGE,
    'DELETE': LOGENTRY_ACTION_DELETION,
}

def get_client_ip(request):
    """Get client IP address from request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def record_operation_log(**kwargs: Any):
    """Create an OperationLog entry only when operation logging is enabled."""
    if not getattr(settings, 'IOE_ENABLE_OPERATION_LOGS', False):
        return None

    from inventory.models import OperationLog

    try:
        payload = dict(kwargs)
        operator = payload.get('operator')
        related_object = payload.pop('related_object', None)
        related_content_type = payload.get('related_content_type')
        related_object_id = payload.get('related_object_id')

        if related_object is not None:
            payload['related_content_type'] = ContentType.objects.get_for_model(related_object)
            payload['related_object_id'] = related_object.pk or 0
        elif related_content_type is None or related_object_id is None:
            if operator is None:
                logger.warning("Skipping operation log without operator: %s", payload)
                return None
            payload['related_content_type'] = ContentType.objects.get_for_model(User)
            payload['related_object_id'] = operator.pk or 0

        return OperationLog.objects.create(**payload)
    except Exception:
        logger.warning("Failed to record operation log", exc_info=True)
        return None


def record_system_log_entry(**kwargs: Any):
    """Create a Django admin LogEntry only when admin logging is enabled."""
    if not getattr(settings, 'IOE_ENABLE_ADMIN_LOGS', False):
        return None

    try:
        payload = dict(kwargs)
        action_type = payload.pop('action_type', None)
        if 'action_flag' not in payload:
            payload['action_flag'] = LEGACY_LOG_ACTION_FLAGS.get(action_type, LOGENTRY_ACTION_CHANGE)
        payload.setdefault('content_type', None)
        if payload.get('content_type_id') in {0, '0', ''}:
            payload.pop('content_type_id', None)
        if 'object_id' in payload and payload['object_id'] is not None:
            payload['object_id'] = str(payload['object_id'])
        payload.setdefault('object_repr', '')
        payload.setdefault('change_message', '')
        return LogEntry.objects.create(**payload)
    except Exception:
        logger.warning("Failed to record admin log entry", exc_info=True)
        return None

def log_action(user, operation_type, details, related_object=None):
    """
    Log an action in the system.
    
    Args:
        user (User): The user performing the action
        operation_type (str): The type of operation (from OperationLog.OPERATION_TYPES)
        details (str): Details about the operation
        related_object (Model, optional): The object related to this operation
    """
    return record_operation_log(
        operator=user,
        operation_type=operation_type,
        details=details,
        related_object=related_object,
    )

def log_operation(user, operation_type, details, related_object=None, request=None):
    """
    记录系统操作日志的主要入口函数
    
    参数:
        user (User): 执行操作的用户
        operation_type (str): 操作类型（来自OperationLog.OPERATION_TYPES）
        details (str): 操作详情
        related_object (Model, optional): 与操作相关的对象
        request (HttpRequest, optional): 当前请求对象，用于获取IP等信息
        
    返回:
        OperationLog: 创建的日志记录对象
    """
    try:
        # 准备日志内容
        log_details = details
        
        # 如果提供了请求对象，添加额外信息
        if request:
            ip = get_client_ip(request)
            agent = request.META.get('HTTP_USER_AGENT', 'Unknown')
            path = request.path
            
            # 将额外信息添加到详细信息中
            if isinstance(details, dict):
                details.update({
                    'ip': ip,
                    'user_agent': agent,
                    'path': path
                })
                log_details = json.dumps(details)
            else:
                # 如果详细信息是字符串，附加额外信息
                log_details = f"{details} [IP: {ip}, 路径: {path}]"
        
        # 使用事务保证日志记录的原子性
        with transaction.atomic():
            return log_action(user, operation_type, log_details, related_object)
    
    except Exception as e:
        # 记录错误但不影响主程序流程
        logger.error(f"记录操作日志时出错: {str(e)}", exc_info=True)
        return None

def log_view_access(operation_type):
    """Decorator to log access to views."""
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Get the result first
            result = view_func(request, *args, **kwargs)
            
            # Don't log for anonymous users
            if request.user.is_authenticated:
                try:
                    # Prepare details
                    details = {
                        'view': view_func.__name__,
                        'path': request.path,
                        'method': request.method,
                        'ip': get_client_ip(request)
                    }
                    
                    # Log the access
                    record_operation_log(
                        operator=request.user,
                        operation_type=operation_type,
                        details=f"Accessed {view_func.__name__}: {json.dumps(details)}"
                    )
                except Exception as e:
                    # Just log the error but don't affect the view's execution
                    logger.error(f"Error logging view access: {str(e)}", exc_info=True)
            
            return result
        return wrapper
    return decorator

def log_exception(func):
    """Decorator to log exceptions in functions."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # 修复：不再尝试在extra中传递args参数，以避免与LogRecord内部的args冲突
            logger.error(
                f"Exception in {func.__name__}: {str(e)}",
                exc_info=True,
                extra={
                    'function_name': func.__name__,
                    'error_message': str(e),
                    'traceback_str': traceback.format_exc()
                }
            )
            raise
    return wrapper 
