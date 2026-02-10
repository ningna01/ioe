from django.contrib import admin

from inventory.models import UserWarehouseAccess


@admin.register(UserWarehouseAccess)
class UserWarehouseAccessAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'warehouse',
        'is_default',
        'is_active',
        'permission_bits',
        'updated_at',
    )
    list_filter = ('is_default', 'is_active', 'warehouse')
    search_fields = ('user__username', 'warehouse__name', 'warehouse__code')
    ordering = ('user__username', 'warehouse__name')
