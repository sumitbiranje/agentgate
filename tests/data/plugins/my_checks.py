from agentgate.checks import CheckOutcome


def polite(trace, case, word="please"):
    return word in trace.final_output.lower()


def with_message(trace, case):
    return (False, "custom says no")


def outcome(trace, case):
    return CheckOutcome(True, "fine", score=0.9)


def crashes(trace, case):
    raise RuntimeError("bug in check")


NOT_CALLABLE = 3
