from agent_system.plan_parser import extract_json_object


def test_pure_json():
    text = '{"objective":"x","tasks":[{"id":"task001","description":"d","acceptance":["a"],"validation":["v"],"files":[]}]}'
    obj = extract_json_object(text)
    assert obj["objective"] == "x"
    assert len(obj["tasks"]) == 1


def test_json_with_prefix_text():
    text = 'Here is the plan: {"objective":"x","tasks":[{"id":"task001","description":"d","acceptance":["a"],"validation":["v"],"files":[]}]} hope you like it'
    obj = extract_json_object(text)
    assert obj is not None
    assert obj["objective"] == "x"


def test_json_with_suffix_text():
    text = '{"objective":"x","tasks":[]} Some suffix text with {braces} that is not JSON.'
    obj = extract_json_object(text)
    assert obj is not None
    assert obj["objective"] == "x"


def test_truncated_json_not_accepted():
    text = '{"objective":"x","tasks":['
    obj = extract_json_object(text)
    assert obj is None


def test_no_json_returns_none():
    text = "no json here at all"
    obj = extract_json_object(text)
    assert obj is None


def test_first_object_when_multiple():
    text = '{"a":1} {"b":2}'
    obj = extract_json_object(text)
    assert obj["a"] == 1
