#!/usr/bin/env python
"""Test Prompt 2.0 · Django 管理入口"""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入 Django，请先激活虚拟环境：.venv\\Scripts\\activate"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
