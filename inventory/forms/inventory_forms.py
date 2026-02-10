from django import forms

from inventory.models import InventoryTransaction, Product, Warehouse


class InventoryTransactionForm(forms.ModelForm):
    """入库表单（支持多仓库）"""
    
    warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.filter(is_active=True),
        label='仓库',
        help_text='请选择入库的目标仓库',
        empty_label='请选择仓库',
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-control form-select',
            'aria-label': '仓库',
            'style': 'height: 48px; font-size: 16px;'
        })
    )
    
    class Meta:
        model = InventoryTransaction
        fields = ['warehouse', 'product', 'quantity', 'notes']
        widgets = {
            'product': forms.Select(attrs={
                'class': 'form-control form-select',
                'aria-label': '商品',
                'style': 'height: 48px; font-size: 16px;'
            }),
            'quantity': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '1',
                'step': '1',
                'placeholder': '数量',
                'inputmode': 'numeric',  # 在移动设备上显示数字键盘
                'aria-label': '数量',
                'autocomplete': 'off',  # 防止自动填充
                'pattern': '[0-9]*',  # HTML5验证，只允许数字
                'style': 'height: 48px; font-size: 16px;'
            }),
            'notes': forms.Textarea(attrs={
                'rows': 3,
                'class': 'form-control',
                'placeholder': '备注信息',
                'aria-label': '备注'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 使用select_related优化查询
        self.fields['product'].queryset = Product.objects.filter(is_active=True).select_related('category')
        
        # 设置默认仓库为初始值
        try:
            default_warehouse = Warehouse.objects.get(is_default=True)
            self.fields['warehouse'].initial = default_warehouse.pk
        except Warehouse.DoesNotExist:
            pass  # 没有默认仓库时不设置初始值
        
        # 添加响应式布局的辅助类
        for field in self.fields.values():
            field.widget.attrs.update({
                'class': field.widget.attrs.get('class', '') + ' mb-2',  # 添加下边距
            })
    
    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')
        if quantity is not None and quantity <= 0:
            raise forms.ValidationError('数量必须大于0')
        return quantity
    
    def clean_warehouse(self):
        warehouse = self.cleaned_data.get('warehouse')
        if warehouse and not warehouse.is_active:
            raise forms.ValidationError('所选仓库已被禁用，请选择其他仓库')
        return warehouse 