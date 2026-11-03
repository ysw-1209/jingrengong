"""一个故意留了 bug 的小模块，用来试智能体：mean 对空列表的处理有问题。"""


def mean(nums):
    if not nums:
        return 0          # BUG：空列表应该报错，默默返回 0 会把错误吞掉
    total = 0
    for n in nums:
        total += n
    return total / len(nums)


def median(nums):
    if not nums:
        raise ValueError("median() 不接受空列表")
    ordered = sorted(nums)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summary(nums):
    return {"n": len(nums), "mean": mean(nums), "median": median(nums)}
