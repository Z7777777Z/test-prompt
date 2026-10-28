"""
URL 路由
保留原型里的 *.html 文件名，使左侧菜单 / 页面内链接无需改动即可访问。
"""
from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

from . import views, views_db, views_fs, views_import, views_llm

urlpatterns = [
    re_path(r"^$", views.index),
    re_path(r"^prompt_dis_2\.0\.html$", views.prompt_dis, name="prompt_dis"),
    re_path(r"^prompt_list\.html$", views.prompt_list, name="prompt_list"),
    re_path(r"^table_relation\.html$", views.table_relation, name="table_relation"),
    re_path(r"^api/prompt/save/$", csrf_exempt(views.prompt_save), name="prompt_save"),
    re_path(r"^api/prompt/delete/$", csrf_exempt(views.prompt_delete), name="prompt_delete"),
    re_path(r"^api/evaluate/save/$", csrf_exempt(views.evaluate_save), name="evaluate_save"),
    # 真实调用大模型（2.0 功能完善）：单次调试 / 批量调试 + 模型自动判定
    re_path(r"^api/llm/chat/$", csrf_exempt(views_llm.llm_chat), name="llm_chat"),
    re_path(r"^api/llm/batch/$", csrf_exempt(views_llm.llm_batch), name="llm_batch"),
    # Excel(.xlsx) 用例导入
    re_path(r"^api/import/xlsx/$", csrf_exempt(views_import.import_xlsx), name="import_xlsx"),
    # 原生文件对话框（表结构 · SQLite 的「路径」浏览…/创建…）
    re_path(r"^api/fs/pick/$", csrf_exempt(views_fs.fs_pick), name="fs_pick"),
    # 表结构 · 「连接数据库」真实连接用户填写的库并读取其表结构
    re_path(r"^api/db/connect/$", csrf_exempt(views_db.db_connect), name="db_connect"),
    # 表结构 · 生成表结构说明：数据库中没有注释的表/字段，用硅基流动 AI 补充备注
    re_path(r"^api/schema/desc/$", csrf_exempt(views_db.schema_desc), name="schema_desc"),
    re_path(r"^score_pompt_list\.html$", views.score_stub),
    re_path(r"^(?P<name>[A-Za-z_][A-Za-z0-9_\-]*\.html)$", views.proto),
]
