"""
仓库管理表单
用于仓库的创建和编辑
"""

from django import forms
from django.core.validators import MinLengthValidator, RegexValidator
from django.utils.text import capfirst
from inventory.models import Warehouse


class WarehouseForm(forms.ModelForm):
    """仓库表单"""
    
    # 仓库编码验证器：只能包含字母、数字和下划线
    code_validator = RegexValidator(
        regex=r'^[a-zA-Z0-9_]+$',
        message='仓库编码只能包含字母、数字和下划线'
    )
    
    code = forms.CharField(
        max_length=20,
        label=capfirst('仓库编码'),
        help_text='用于程序内部标识，只能包含字母、数字和下划线',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入仓库编码',
            'autocomplete': 'off',
            'aria-label': '仓库编码'
        }),
        validators=[MinLengthValidator(1), code_validator]
    )
    
    name = forms.CharField(
        max_length=100,
        label=capfirst('仓库名称'),
        help_text='仓库的唯一名称',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入仓库名称',
            'autocomplete': 'off',
            'aria-label': '仓库名称'
        })
    )
    
    address = forms.CharField(
        max_length=255,
        required=False,
        label=capfirst('地址'),
        help_text='仓库的详细地址（可选）',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入仓库地址',
            'aria-label': '地址'
        })
    )
    
    phone = forms.CharField(
        max_length=20,
        required=False,
        label=capfirst('联系电话'),
        help_text='仓库的联系电话（可选）',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入联系电话',
            'aria-label': '联系电话'
        })
    )
    
    contact_person = forms.CharField(
        max_length=50,
        required=False,
        label=capfirst('联系人'),
        help_text='仓库的联系人姓名（可选）',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入联系人姓名',
            'aria-label': '联系人'
        })
    )
    
    is_default = forms.BooleanField(
        required=False,
        label=capfirst('设为默认仓库'),
        help_text='将该仓库设为系统的默认仓库（入库时将默认选择此仓库）',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
            'aria-label': '设为默认仓库'
        })
    )
    
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label=capfirst('是否启用'),
        help_text='控制仓库是否参与库存业务逻辑',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
            'aria-label': '是否启用'
        })
    )
    
    class Meta:
        model = Warehouse
        fields = ['name', 'code', 'address', 'phone', 'contact_person', 'is_default', 'is_active']
    
    def clean_name(self):
        """验证仓库名称"""
        name = self.cleaned_data.get('name', '').strip()
        if len(name) < 2:
            raise forms.ValidationError('仓库名称至少需要2个字符')
        
        # 检查名称是否重复（编辑模式下排除自身）
        if self.instance and self.instance.pk:
            if Warehouse.objects.filter(name=name).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('该仓库名称已存在')
        else:
            if Warehouse.objects.filter(name=name).exists():
                raise forms.ValidationError('该仓库名称已存在')
        
        return name
    
    def clean_code(self):
        """验证仓库编码"""
        code = self.cleaned_data.get('code', '').strip()
        if len(code) < 1:
            raise forms.ValidationError('仓库编码不能为空')
        
        # 检查编码是否重复（编辑模式下排除自身）
        if self.instance and self.instance.pk:
            if Warehouse.objects.filter(code=code).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('该仓库编码已存在')
        else:
            if Warehouse.objects.filter(code=code).exists():
                raise forms.ValidationError('该仓库编码已存在')
        
        return code
    
    def clean_is_default(self):
        """验证默认仓库设置"""
        is_default = self.cleaned_data.get('is_default', False)
        
        # 如果设置为默认仓库，检查是否已有其他默认仓库
        if is_default:
            existing_default = Warehouse.objects.filter(is_default=True)
            if self.instance and self.instance.pk:
                existing_default = existing_default.exclude(pk=self.instance.pk)
            if existing_default.exists():
                # 如果存在其他默认仓库，将它们取消默认设置
                existing_default.update(is_default=False)
        
        return is_default
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 为所有字段添加统一样式
        for field_name, field in self.fields.items():
            if 'class' not in field.widget.attrs:
                if isinstance(field.widget, (forms.TextInput, forms.Textarea, forms.NumberInput)):
                    field.widget.attrs['class'] = 'form-control'
                elif isinstance(field.widget, forms.Select):
                    field.widget.attrs['class'] = 'form-control form-select'
                elif isinstance(field.widget, forms.CheckboxInput):
                    field.widget.attrs['class'] = 'form-check-input'
            
            # 添加底部间距
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = field.widget.attrs.get('class', '') + ' mb-2'
            else:
                field.widget.attrs['class'] = field.widget.attrs.get('class', '') + ' mb-3'


class WarehouseSelectionForm(forms.Form):
    """仓库选择表单（用于入库等场景）"""
    
    warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.filter(is_active=True),
        label=capfirst('选择仓库'),
        help_text='请选择入库的目标仓库',
        empty_label='请选择仓库',
        widget=forms.Select(attrs={
            'class': 'form-control form-select',
            'aria-label': '选择仓库'
        })
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 设置默认仓库为初始值
        try:
            default_warehouse = Warehouse.objects.get(is_default=True)
            self.fields['warehouse'].initial = default_warehouse.pk
        except Warehouse.DoesNotExist:
            pass  # 没有默认仓库时不设置初始值