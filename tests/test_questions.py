"""Unit tests for quiz question banks and selection logic."""
import os
import unittest

# Kivy initializes the window on import; tests run fine with the default backend.
import quizgamev3 as qg


def _validate_bank(bank):
    """Every category must have tiers with questions where correct_answer is one of four options."""
    for cat, tiers in bank.items():
        assert isinstance(tiers, dict), f'category {cat!r} must map to difficulty tiers'
        for diff, plist in tiers.items():
            assert plist, f'{cat}/{diff} must be non-empty'
            for i, q in enumerate(plist):
                opts = list(q.options)
                assert len(opts) == 4, f'{cat}/{diff}[{i}]: expected 4 options, got {len(opts)}'
                assert q.correct_answer in opts, (
                    f'{cat}/{diff}[{i}]: correct_answer {q.correct_answer!r} not in options {opts!r}'
                )
                assert q.question and str(q.question).strip(), f'{cat}/{diff}[{i}]: empty question text'


class TestBuiltinQuestions(unittest.TestCase):
    def test_builtin_bank_integrity(self):
        _validate_bank(qg._BUILTIN_QUESTIONS_DATA)

    def test_get_quiz_questions_length_and_integrity(self):
        orig = qg.questions_data
        try:
            qg.questions_data = qg._BUILTIN_QUESTIONS_DATA
            for cat in qg._BUILTIN_QUESTIONS_DATA:
                for diff in ('easy', 'medium', 'hard'):
                    session = hash((cat, diff)) % 10000
                    items = qg.get_quiz_questions(cat, diff, session_seed=session)
                    self.assertEqual(
                        len(items),
                        qg.QUIZ_QUESTIONS_PER_ROUND,
                        f'{cat}/{diff}: wrong round length',
                    )
                    for j, q in enumerate(items):
                        self.assertIn(q.correct_answer, q.options, f'{cat}/{diff} item {j}')
        finally:
            qg.questions_data = orig

    def test_question_from_dict_wrong_list(self):
        q = qg.question_from_dict(
            {
                'question': 'Sample?',
                'correct': 'A',
                'wrong': ['B', 'C', 'D'],
            },
            difficulty='easy',
        )
        self.assertEqual(len(q.options), 4)
        self.assertIn('A', q.options)
        self.assertEqual(q.correct_answer, 'A')

    def test_question_from_dict_options_list(self):
        q = qg.question_from_dict(
            {
                'question': 'Pick one',
                'correct': 'gamma',
                'options': ['alpha', 'beta', 'gamma', 'delta'],
            },
            difficulty='medium',
        )
        self.assertEqual(q.correct_answer, 'gamma')
        self.assertIn('gamma', q.options)


class TestQuestionsJsonRoundTrip(unittest.TestCase):
    def test_load_exported_json_if_present(self):
        path = qg.QUESTIONS_JSON
        if not os.path.isfile(path):
            self.skipTest(f'no {path} — builtin-only install')
        loaded = qg.load_questions_json(path)
        self.assertIsNotNone(loaded)
        _validate_bank(loaded)


if __name__ == '__main__':
    unittest.main()
