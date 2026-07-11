import pytest

from pm_system.agents.analyst import AnalystAgent
from pm_system.agents.base import ContextPackage
from pm_system.costs.ledger import CostLedger
from pm_system.errors import AgentOutputError
from pm_system.llm.client import CostTags, MeteredLLM, MockLLM, extract_json

TAGS = CostTags(project_id="p1", stage="intake", agent="analyst")

VALID_PRD = (
    '{"title": "T", "summary": "S", "user_stories": [{"id": "US-001", "story": "s",'
    ' "acceptance_criteria": [{"id": "AC-001", "given": "g", "when": "w", "then": "t"}]}]}'
)


def _analyst(responses):
    return AnalystAgent(MeteredLLM(MockLLM(responses), CostLedger()), model="test-model")


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('prose\n```json\n{"a": 1}\n```\nmore') == {"a": 1}
    assert extract_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json("[1, 2, 3]")


def test_extract_json_tolerates_backticks_inside_content():
    # A doc artifact whose content contains a Markdown code fence must not
    # truncate the JSON at the inner ``` (fence captured to the last ```).
    text = 'here\n```json\n{"docs": "use ```\\ncode\\n``` here"}\n```'
    assert extract_json(text) == {"docs": "use ```\ncode\n``` here"}


def test_agent_accepts_schema_valid_output():
    prd = _analyst([VALID_PRD]).run(ContextPackage(instructions="go"), TAGS)
    assert prd["user_stories"][0]["id"] == "US-001"


def test_agent_rejects_invalid_json():
    with pytest.raises(AgentOutputError) as excinfo:
        _analyst(["not json at all"]).run(ContextPackage(instructions="go"), TAGS)
    assert "JSON" in excinfo.value.defects[0]


def test_agent_rejects_schema_violation():
    with pytest.raises(AgentOutputError) as excinfo:
        _analyst(['{"title": "T"}']).run(ContextPackage(instructions="go"), TAGS)
    assert "schema violation" in excinfo.value.defects[0]


def test_context_cap_is_a_hard_stop():
    from pm_system.errors import ContextOverflowError

    agent = _analyst([VALID_PRD])
    agent.max_context_chars = 100
    with pytest.raises(ContextOverflowError):
        agent.run(ContextPackage(instructions="x" * 200), TAGS)
    # nothing was spent: the cap fires before the LLM call
    assert agent.llm.client.calls == []


def test_prompt_includes_playbook_feedback_and_artifacts():
    agent = _analyst([VALID_PRD])
    assert "Analyst Playbook" in agent.system_prompt()
    prompt = agent.user_prompt(
        ContextPackage(instructions="go", artifacts={"ticket": {"k": "v"}}, feedback="fix X")
    )
    assert "go" in prompt and '"k": "v"' in prompt and "fix X" in prompt
