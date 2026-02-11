from django.contrib.auth.views import LoginView
from django.urls import reverse

from inventory.services.user_mode_service import is_sales_focus_user


class RoleAwareLoginView(LoginView):
    """按用户模式决定登录后落点。"""

    def get_success_url(self):
        # 保留 next 参数优先级，避免打断深链接回跳。
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url

        if is_sales_focus_user(self.request.user):
            return reverse('sale_create')

        return super().get_success_url()
