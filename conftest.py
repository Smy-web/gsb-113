"""把仓库根加进 sys.path，测试与复现脚本统一用 backend.xxx 导入。"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
