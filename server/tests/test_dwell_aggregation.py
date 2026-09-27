"""逐题平均停留的聚合口径。

心跳每 60 秒就给当前题提交一条 question_dwell，所以同一名学生同一道题会有
多条事件。聚合必须先按 (作答, 题目) 合并成「这人这题一共停留多久」，再对人数
取平均；直接「总时长 / 事件数」会被段数摊薄，在同一道题上停留越久反而显得越短。
"""
import unittest

from app.services.assessment_service import AssessmentService as A

Q1 = "11111111-1111-1111-1111-111111111111"
Q2 = "22222222-2222-2222-2222-222222222222"
A1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
A2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def dwell(attempt_id, qid, ms):
    return (attempt_id, {"question_id": qid, "duration_ms": ms})


class AverageDwellPerQuestionTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(A.average_dwell_per_question([]), {})

    def test_single_event_per_student_is_its_own_duration(self):
        rows = [dwell(A1, Q1, 45_000), dwell(A2, Q1, 75_000)]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 60.0})

    def test_heartbeat_segments_are_summed_not_averaged(self):
        """回归：停留 150 秒被心跳切成 3 条，不能被摊薄成 50 秒。"""
        rows = [dwell(A1, Q1, 60_000), dwell(A1, Q1, 60_000), dwell(A1, Q1, 30_000)]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 150.0})

    def test_averages_across_students_with_different_segment_counts(self):
        rows = [
            dwell(A1, Q1, 60_000),  # A 这题共 120 秒，两条
            dwell(A1, Q1, 60_000),
            dwell(A2, Q1, 30_000),  # B 这题共 30 秒，一条
        ]
        # (120 + 30) / 2 = 75 秒；按事件数摊薄会算成 150/3 = 50 秒
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 75.0})

    def test_questions_are_kept_separate(self):
        rows = [dwell(A1, Q1, 10_000), dwell(A1, Q2, 20_000), dwell(A2, Q2, 40_000)]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 10.0, Q2: 30.0})

    def test_sub_second_total_keeps_one_decimal(self):
        rows = [dwell(A1, Q1, 400), dwell(A1, Q1, 400)]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 0.8})

    def test_malformed_payloads_are_skipped(self):
        rows = [
            (A1, None),
            (A1, {}),
            (A1, {"question_id": None, "duration_ms": 1000}),
            (A1, {"question_id": Q1, "duration_ms": None}),
            (A1, {"question_id": Q1, "duration_ms": "oops"}),
            (A1, {"question_id": Q1, "duration_ms": 2_000}),
        ]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 2.0})

    def test_string_duration_is_coerced(self):
        rows = [dwell(A1, Q1, "3000")]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 3.0})

    def test_same_question_across_attempts_is_not_merged_into_one_sample(self):
        """两份作答是两个人，不能当成一个人算。"""
        rows = [dwell(A1, Q1, 10_000), dwell(A2, Q1, 30_000)]
        self.assertEqual(A.average_dwell_per_question(rows), {Q1: 20.0})


if __name__ == "__main__":
    unittest.main()
