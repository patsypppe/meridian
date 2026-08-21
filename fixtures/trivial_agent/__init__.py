"""A second system under test that shares nothing with the first.

It exists to make `test_same_suite_runs_on_two_adapters` meaningful: the same
task, graded the same way, reached through two adapters that have no code in
common. It calls no model, so what the test measures is the adapter contract
rather than the agent.
"""
