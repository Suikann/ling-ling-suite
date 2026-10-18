# -*- coding: utf-8 -*-
"""
發譜預覽「聲部」步的規則測試

對專案模型與假 HuaXia 測：按下發譜時讀出可以挑的曲目的聲部；每個泠靈聲部依序以記住的配對、
泠靈的「同一聲部」規則對到 HuaXia 聲部，對不上的預設新建；名稱規則對出多個時不自動選；
兩個泠靈聲部指到同一個時擋下；手動挑的配對記進專案；算出要寫的內容（人數記法、聲部組、覆寫、
「本專案沒有」的聲部預設不建），永遠不刪 HuaXia 的聲部。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.huaxia_client import HuaXiaPart
from test_publish_preflight import PrepareTestCase


class TestPrepareReadsParts(PrepareTestCase):
    """按下發譜時一起讀出可以挑的曲目的聲部（已封存的曲目不讀）"""

    def test_parts_of_unarchived_pieces_ordered_like_huaxia(self):
        erhu = self.huaxia.add_part(self.fenghuo, "二胡1", needed="6-8", needed_min=6, section="拉弦", sort=2)
        di = self.huaxia.add_part(self.fenghuo, "梆笛", sort=1)
        archived = self.huaxia.add_piece("十面埋伏", archived=True)
        self.huaxia.add_part(archived, "琵琶")
        readiness = self.service.prepare()
        self.assertEqual(readiness.parts, {
            self.fenghuo: [
                HuaXiaPart(di, self.fenghuo, "梆笛", "1", 1, "吹管", 1),
                HuaXiaPart(erhu, self.fenghuo, "二胡1", "6-8", 6, "拉弦", 2),
            ],
        })


if __name__ == "__main__":
    unittest.main()
