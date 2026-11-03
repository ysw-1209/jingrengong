"""配套测试：6 个用例，其中"空列表应报错"现在是失败的（正是要给智能体修的那个）。
运行：python samples/test_stats.py    或    pytest samples -q
"""
import unittest

from stats import mean, median, summary


class TestStats(unittest.TestCase):
    def test_mean_basic(self):
        self.assertAlmostEqual(mean([1, 2, 3, 4]), 2.5)

    def test_mean_negative(self):
        self.assertAlmostEqual(mean([-1, 1]), 0.0)

    def test_mean_single(self):
        self.assertEqual(mean([7]), 7)

    def test_median_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_median_even(self):
        self.assertAlmostEqual(median([1, 2, 3, 4]), 2.5)

    def test_mean_empty_should_raise(self):
        with self.assertRaises(ValueError):
            mean([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
