# -*- coding: utf-8 -*-
"""写入「调试列表」示例数据。

    python manage.py seed_prompts            # prompt 表为空时写入（默认）
    python manage.py seed_prompts --force    # 先清空 prompt 表再写入
"""
from django.core.management.base import BaseCommand

from tp import db


class Command(BaseCommand):
    help = "写入调试列表示例数据（默认仅在 prompt 表为空时写入）"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="先清空 prompt 表再写入示例数据")

    def handle(self, *args, **options):
        db.init_db()
        n = db.seed_prompts(force=options["force"])
        if n:
            self.stdout.write(self.style.SUCCESS(f"已写入示例调试记录 {n} 条"))
        else:
            self.stdout.write("prompt 表已有数据，未写入（如需覆盖请加 --force）")
