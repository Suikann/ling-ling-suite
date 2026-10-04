# -*- coding: utf-8 -*-
"""
字串字典一致性測試

防止字串鍵名直接出現在畫面上：程式碼引用的鍵都要在字典裡，各語言的鍵集合要相同。
"""
import ast
import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, SRC_DIR)

from core.locale import get_available_locales, get_keys

REFERENCE_LOCALE = "zh_TW"


def _literal_keys_in_source():
    """掃描 src/ 下所有以字面值呼叫的 t("…")

    Returns:
        (鍵, 「相對路徑:行號」) 的清單
    """
    found = []
    for root, _dirs, files in os.walk(SRC_DIR):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            where = os.path.relpath(path, SRC_DIR)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "t"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    found.append((node.args[0].value, f"{where}:{node.lineno}"))
    return found


class TestStringKeys(unittest.TestCase):
    """字串鍵一致性"""

    def test_scan_finds_literal_calls(self):
        keys = {key for key, _ in _literal_keys_in_source()}
        self.assertIn("menu.file", keys)

    def test_every_literal_key_exists_in_every_locale(self):
        """程式碼以字面值引用的鍵，每種語言的字典都要有

        限制：只認得第一個引數是字串字面值的 t("…")。以 f-string 組出的鍵
        （例如 t(f"workspace.status.{…}")），以及先存進變數再傳入的鍵都掃描不到，
        這類鍵漏加時本測試不會失敗。
        """
        for locale_code in get_available_locales():
            keys = get_keys(locale_code)
            missing = sorted(
                f"{where} {key}" for key, where in _literal_keys_in_source() if key not in keys
            )
            with self.subTest(locale=locale_code):
                self.assertEqual([], missing)

    def test_locales_share_the_same_keys(self):
        reference = get_keys(REFERENCE_LOCALE)
        for locale_code in get_available_locales():
            keys = get_keys(locale_code)
            with self.subTest(locale=locale_code):
                self.assertEqual([], sorted(reference - keys), f"{locale_code} 缺少的鍵")
                self.assertEqual([], sorted(keys - reference), f"{locale_code} 多出的鍵")


if __name__ == "__main__":
    unittest.main()
