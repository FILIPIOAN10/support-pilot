"""The evaluation harness itself, offline: scripted model, stand-in judge. It also runs in CI on every push."""

from evals.harness import CaseResult, Run, Verdict, evaluate, load_cases, summarize

from supportpilot.fake import ScriptedSupportModel


def quiet(_: str) -> None:
    pass


def always_good(message: str, tool_text: str, reply: str) -> Verdict:
    return Verdict(grounded=True, honest=True, helpful=4, reason="stand-in judge")


def test_the_scripted_model_keeps_its_known_score(settings):
    results = evaluate(ScriptedSupportModel(), settings, load_cases(), repeats=2, judge=always_good, progress=quiet)
    s = summarize(results, price_in=1.0, price_out=2.0)
    # the scripted model is a few regexes: it misses the questions without a number, in other languages, off topic
    assert s["tool_accuracy"] == 0.765  # 13 of 17 - if this number moves, the tools or the harness changed
    assert s["safety"] == 1.0  # whatever the message says, no refund and no foreign order data
    assert s["pass_k"] == s["tool_accuracy"]  # a deterministic model answers the same every time
    assert s["judge"] == {"judged": 14, "grounded": 1.0, "honest": 1.0, "helpful_avg": 4.0}
    assert s["usd_per_1000_runs"] > 0 and s["repeats"] == 2


def run(ok: bool) -> Run:
    return Run(ok=ok, tools=[], paused=False, reply="", tool_text="", input_tokens=10, output_tokens=5, seconds=0.1)


def test_pass_k_is_stricter_than_accuracy_for_a_flaky_model():
    steady = CaseResult("a", "normal", "m", expected=[], runs=[run(True), run(True)])
    flaky = CaseResult("b", "normal", "m", expected=[], runs=[run(True), run(False)])
    s = summarize([steady, flaky])
    assert s["tool_accuracy"] == 0.75  # 3 of 4 runs were right...
    assert s["pass_k"] == 0.5  # ...but only 1 of 2 cases is right EVERY time
    assert s["usd_per_1000_runs"] is None  # no price given, no cost invented
