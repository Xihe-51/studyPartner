import unittest
from types import SimpleNamespace

from app.services.assessment_service import AssessmentService as A


def code_question(starter=None):
    return SimpleNamespace(question_type="code", starter_code=starter)


def single_question():
    return SimpleNamespace(question_type="single", starter_code=None)


class StripUntouchedStarterTests(unittest.TestCase):
    """编程题预填的起始代码不能算作答。"""

    def test_untouched_starter_is_treated_as_no_answer(self):
        starter = "n = int(input())\n# 在这里写你的代码\n"
        self.assertIsNone(A._strip_untouched_starter(code_question(starter), starter))

    def test_ignores_leading_and_trailing_whitespace(self):
        starter = "n = int(input())\n"
        self.assertIsNone(
            A._strip_untouched_starter(code_question(starter), "\n  n = int(input())\n\n")
        )

    def test_edited_code_is_kept(self):
        starter = "n = int(input())\n# 在这里写你的代码\n"
        edited = "n = int(input())\nprint(n * 2)\n"
        self.assertEqual(A._strip_untouched_starter(code_question(starter), edited), edited)

    def test_code_expected_output_reference_answer_is_kept(self):
        """只有起始代码那一份要归一；与它不同的任何内容都照常保留。"""
        starter = "print(1)"
        self.assertEqual(
            A._strip_untouched_starter(code_question(starter), "print(1)  # 一样的前缀但更长"),
            "print(1)  # 一样的前缀但更长",
        )

    def test_question_without_starter_keeps_answer(self):
        self.assertEqual(A._strip_untouched_starter(code_question(None), "print(1)"), "print(1)")
        self.assertEqual(A._strip_untouched_starter(code_question(""), "print(1)"), "print(1)")

    def test_non_code_question_is_untouched(self):
        self.assertEqual(A._strip_untouched_starter(single_question(), "A"), "A")

    def test_empty_answer_stays_empty(self):
        for value in (None, ""):
            self.assertEqual(A._strip_untouched_starter(code_question("print(1)"), value), value)

    def test_non_string_answer_is_not_mangled(self):
        value = ["A", "B"]
        self.assertEqual(A._strip_untouched_starter(code_question("print(1)"), value), value)


if __name__ == "__main__":
    unittest.main()
