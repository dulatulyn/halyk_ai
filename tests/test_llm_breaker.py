from solution import llm


def test_breaker_opens_after_repeated_failures():
    llm.reset_breaker()
    assert not llm.breaker_open("openai")
    for _ in range(llm.FAILURE_LIMIT):
        llm.note_failure("openai")
    assert llm.breaker_open("openai")


def test_breaker_is_per_provider():
    llm.reset_breaker()
    for _ in range(llm.FAILURE_LIMIT):
        llm.note_failure("anthropic")
    assert llm.breaker_open("anthropic")
    assert not llm.breaker_open("openai")


def test_success_resets_the_counter():
    llm.reset_breaker()
    for _ in range(llm.FAILURE_LIMIT - 1):
        llm.note_failure("gemini")
    llm.note_success("gemini")
    llm.note_failure("gemini")
    assert not llm.breaker_open("gemini")


def test_dead_provider_drops_out_of_the_queue():
    llm.reset_breaker()
    for _ in range(llm.FAILURE_LIMIT):
        llm.note_failure("anthropic")
    assert "anthropic" not in llm.live_providers()


def test_used_models_records_what_actually_answered():
    llm.reset_breaker()
    llm.note_success("openai")
    assert llm.used_models() == [llm.MODELS["openai"]]
