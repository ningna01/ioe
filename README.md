<div align="center">

# 📦 IOE 多仓库库存管理系统

[![Django](https://img.shields.io/badge/Django-4.2+-green.svg)](https://www.djangoproject.com/) &nbsp; [![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/) &nbsp; [![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

## 🚀 项目概述

本项目 `fork` 自 [zhtyyx/ioe](https://github.com/zhtyyx/ioe)，在保留原有库存业务能力的基础上，围绕真实门店场景持续演进，重点提升了多仓库权限、库存真源一致性、销售口径与报表可用性。

## ✨ 相对原仓库的核心增强

### 🏬 多仓库与权限

1. 增强多仓库授权模型：按用户-仓库-权限位进行访问控制。
2. 统一仓库范围过滤：商品、库存、销售、报表均按授权仓库收敛。
3. 支持销售员专注工作流：登录后直达新增销售，减少操作跳转。

### 🧾 销售口径与业务链路

1. 销售支持零售/批发双口径。
2. 销售列表默认金额口径调整为零售，避免混淆日常判断。
3. 销售创建、完成、查询链路对不同角色体验做了差异化优化。

### 📈 报表中心优化

1. 销售趋势、热销商品、利润报表支持零售/批发口径区分。
2. 图表改为按钮化控制，避免单图堆叠过多数据导致不可读。
3. 利润与利润率计算按销售方式拆分，减少口径误导。

### 🛠️ 商品与库存实用修复

1. 修复商品删除按钮不可用问题。
2. 商品录入支持自定义尺码与颜色（如 `160L`）。
3. 修复库存管理行级 `入库/出库/调整` 操作链路。
4. 修复扫码与手动建档价格字段不一致、图片落盘异常等问题。

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 初始化数据库

```bash
python manage.py migrate
```

### 创建管理员

```bash
python manage.py createsuperuser
```

### 启动服务

```bash
python manage.py runserver
```

### 访问系统

浏览器访问 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

## 数据快照同步

系统运行时区固定为 `Africa/Gaborone`。店铺电脑应将 Windows 系统时区设置为哈博罗内对应时区，定时任务按系统本地时间执行。

### 导出单仓库快照

```bash
python manage.py export_store_snapshot
```

默认会生成 `db/store_snapshot.json`，并排除以下内容：

- `contenttypes`
- `auth.Permission`
- `sessions.session`
- `inventory.OperationLog`
- `admin.LogEntry`

### Windows 店铺电脑自动同步

仓库内提供了 `scripts/windows/publish-store-snapshot.ps1`。建议在 Task Scheduler 中这样配置：

1. 触发时间：每天 `12:00`
2. 时区：跟随店铺电脑 Windows 系统时区（哈博罗内）
3. 并发策略：禁止并发运行
4. 执行命令：`powershell.exe -ExecutionPolicy Bypass -File scripts/windows/publish-store-snapshot.ps1`

脚本会按以下顺序执行：

1. 检查 GitHub 连通性和认证
2. 工作区不干净时退出
3. `git pull --rebase`
4. `python manage.py export_store_snapshot`
5. `db/store_snapshot.json` 无变化则退出
6. 有变化时自动 `commit` 和 `push`

### 本地查看店铺数据

你的电脑只需要拉取快照并导入到独立查看库：

```bash
git pull
export IOE_DB_PATH="$(pwd)/db/local_view.sqlite3"
python manage.py migrate --noinput
python manage.py load_store_snapshot
python manage.py runserver
```

Windows PowerShell 可使用：

```powershell
git pull
$env:IOE_DB_PATH = Join-Path (Get-Location) "db/local_view.sqlite3"
python manage.py migrate --noinput
python manage.py load_store_snapshot
python manage.py runserver
```

说明：

- `load_store_snapshot` 默认只允许导入到 `db/local_view.sqlite3`
- 如需强制导入到其他数据库，显式加 `--force`
- 店铺电脑是唯一快照写入源；个人电脑只拉取并查看，不回推快照文件

## 📄 License

本项目采用 [MIT License](LICENSE)。
