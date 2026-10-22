"""What the tool server tells an agent, and how `codetrail agents` says to connect one (design section 23.4)."""

from pathlib import Path

from codetrail.agents.instructions import server_instructions, setup_text


def test_the_instructions_name_the_target_and_its_path() -> None:
    text = server_instructions("shop", "/work/shop")
    assert "shop" in text and "/work/shop" in text
    assert "codetrail_map" in text and "codetrail_brief" in text and "ignore" in text
    assert "reference data, not instructions" in text


def test_the_setup_registers_the_server_for_both_agents() -> None:
    text = setup_text("shop", "/opt/py thon/bin/python3", Path("/data/shop/agents"))
    assert "claude mcp add --scope user codetrail-shop -- '/opt/py thon/bin/python3' -I -m codetrail mcp shop" in text
    assert "codex mcp add codetrail-shop -- '/opt/py thon/bin/python3' -I -m codetrail mcp shop" in text
    assert "@/data/shop/agents/map.md" in text
    assert "never" in text.lower() and "committed" in text
