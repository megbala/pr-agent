"""
Tiny demo module used to test the PR review agent end-to-end. Not part of the
agent itself -- see README.md step 5 ("open a test PR with an intentional bug").
"""


def average(values):
    total = sum(values)
    return total / len(values)


def load_config(path):
    f = open(path)
    data = f.read()
    return data
