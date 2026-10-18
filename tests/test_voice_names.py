# -*- coding: utf-8 -*-
"""
聲部名稱比對測試

表格以 #77 盤點到的實際寫法為主：同一聲部（結尾編號互通）與同一樂器（去掉結尾編號）兩種比對各列預期結果。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.voice_names import same_instrument, same_voice

SAME_VOICE = [
    # 結尾編號的各種寫法互通
    ("二胡 I", "二胡1", True),
    ("二胡 I", "二胡一", True),
    ("二胡 I", "二胡 1", True),
    ("二胡 I", "二胡Ⅰ", True),
    ("二胡 I", "二胡１", True),
    ("二胡1", "二胡１", True),
    ("二胡 II", "二胡2", True),
    ("二胡 II", "二胡二", True),
    ("二胡Ⅲ", "二胡 3", True),
    ("Violin I", "violin 1", True),
    ("Flute 10", "Flute X", True),
    ("Flute 10", "Flute十", True),
    # 編號不同、有編號與沒編號都不同
    ("二胡 I", "二胡 II", False),
    ("二胡", "二胡 I", False),
    ("二胡", "二胡1", False),
    # 前面不是中文字或分隔符號、或不單獨成段的羅馬字母不算編號
    ("Di II", "Di 2", True),
    ("Di II", "D 2", False),
    ("Di", "D 1", False),
    ("Di", "di", True),
    ("Horn in F", "horn in f", True),
    ("Clarinet in C", "Clarinet in C", True),
    ("Clarinet in C", "Clarinet in 100", False),
    ("Horn in F", "Horn in F 1", False),
    # 格式：大小寫、全半形、空白、分隔符號與括號
    ("Cello", "CELLO", True),
    ("Cello", "Ｃｅｌｌｏ", True),
    ("低音 大提琴", "低音大提琴", True),
    ("Alto-Sax", "alto sax", True),
    ("二胡（I）", "二胡1", True),
    ("二胡_I", "二胡.1", True),
    # 先切結尾編號再去分隔符號：「新笛Ⅰ、Ⅱ」不會黏成 3
    ("新笛Ⅰ、Ⅱ", "新笛 I、II", True),
    ("新笛Ⅰ、Ⅱ", "新笛III", False),
    ("新笛Ⅰ、Ⅱ", "新笛 II", False),
    # 多聲部與兼奏整串是一個聲部
    ("竹笛Ⅰ 兼口笛", "竹笛I兼口笛", True),
    ("竹笛Ⅰ 兼口笛", "竹笛Ⅰ", False),
    ("梆笛 曲笛", "梆笛曲笛", True),
    ("梆笛 曲笛", "梆笛", False),
    # 只看結尾：開頭與中間的數字不受影響
    ("2高笙", "2高笙", True),
    ("2高笙", "高笙", False),
    ("2高笙", "二高笙", False),
    ("三弦", "三弦", True),
    ("三弦", "3弦", False),
    # 不做樂器別名
    ("低音大提琴", "低音提琴", False),
    ("低音大提琴", "大提琴", False),
    ("Cello", "大提琴", False),
    ("高笙", "高音笙", False),
    ("箏", "古箏", False),
]

SAME_INSTRUMENT = [
    # 去掉結尾編號後比對：同一種樂器的各個聲部與沒編號的寫法都相同
    ("二胡 I", "二胡 II", True),
    ("二胡 I", "二胡", True),
    ("二胡1", "二胡三", True),
    ("二胡Ⅰ", "二胡１", True),
    ("Violin I", "Violin 2", True),
    ("Di II", "Di", True),
    ("Di II", "D", False),
    ("Horn in F", "Horn in F 2", True),
    ("Clarinet in C", "clarinet in c", True),
    ("Alto－Sax 1", "alto sax", True),
    # 多聲部與兼奏整串比對，只去掉最後的編號
    ("新笛Ⅰ、Ⅱ", "新笛 I", False),
    ("新笛Ⅰ、Ⅱ", "新笛I、III", True),
    ("竹笛Ⅰ 兼口笛", "竹笛", False),
    ("竹笛Ⅰ 兼口笛", "口笛", False),
    # 開頭與中間的數字是名稱的一部分
    ("2高笙", "高笙", False),
    ("2高笙", "2高笙 1", True),
    ("三弦", "弦", False),
    # 不做樂器別名
    ("低音大提琴", "低音提琴", False),
    ("低音大提琴 I", "大提琴", False),
    ("Cello", "大提琴", False),
    ("Cello 1", "cello", True),
]


class TestSameVoice(unittest.TestCase):
    """同一聲部：結尾編號互通，有編號與沒編號、編號不同都不同"""

    def test_table(self):
        for a, b, expected in SAME_VOICE:
            with self.subTest(a=a, b=b):
                self.assertEqual(same_voice(a, b), expected)
                self.assertEqual(same_voice(b, a), expected)


class TestSameInstrument(unittest.TestCase):
    """同一樂器：去掉結尾編號後比對"""

    def test_table(self):
        for a, b, expected in SAME_INSTRUMENT:
            with self.subTest(a=a, b=b):
                self.assertEqual(same_instrument(a, b), expected)
                self.assertEqual(same_instrument(b, a), expected)


if __name__ == "__main__":
    unittest.main()
