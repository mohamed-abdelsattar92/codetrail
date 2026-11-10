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


def test_an_ssh_remote_reaches_the_agent_without_an_at_sign() -> None:
    # An `@` could read as a mention or a file reference to the agent.
    text = server_instructions("acme-web-shop", "git@github.com:acme-org/web-shop.git")
    assert "@" not in text
    assert "github.com:acme-org/web-shop.git" in text
