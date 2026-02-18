from llm_council_mcp.roles import DEFAULT_ROLE, get_role_spec


def test_get_role_spec_by_provider():
    assert get_role_spec("openai/gpt-5.2").name == "Builder"
    assert get_role_spec("anthropic/claude-sonnet-4.5").name == "Reviewer"
    assert get_role_spec("google/gemini-3-pro-preview").name == "Integrator"
    assert get_role_spec("x-ai/grok-4.1-fast").name == "Contrarian"


def test_get_role_spec_unknown_provider_default():
    assert get_role_spec("unknown/model").name == DEFAULT_ROLE.name


def test_build_messages_and_chairman_prompt():
    from llm_council_mcp.roles import build_messages_for_model, chairman_system_prompt

    msgs = build_messages_for_model(
        "openai/gpt-5.2",
        "hello",
        contract_system_messages=[{"role": "system", "content": "contract"}],
        extra_system="extra",
    )
    assert msgs[0]["content"] == "contract"
    assert msgs[-1]["role"] == "user"
    assert "CHAIRMAN MODE" in chairman_system_prompt()
