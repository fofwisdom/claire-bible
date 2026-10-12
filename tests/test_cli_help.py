"""Tests for CLI Help Strings, Descriptions, and Formatting Consistency.

Validates that all subcommands and subparsers provide clear, standardized
help text and descriptions across the entire CLI surface.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import pytest

from claire import cli


def _get_subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def test_top_level_parser_help_and_epilog():
    """Verify top-level parser metadata, description, and quick-reference epilog."""
    parser = cli.build_parser()
    assert parser.prog == "claire"
    assert parser.description and "Autonomous Knowledge Graph" in parser.description
    assert parser.epilog and "Quick Reference:" in parser.epilog
    assert "Detailed Documentation: docs/origin/implementation/COMMANDS.md" in parser.epilog


def test_all_subcommands_have_help_and_description():
    """Verify all top-level subcommands have non-empty help strings and descriptions."""
    parser = cli.build_parser()
    sub_action = _get_subparsers_action(parser)
    assert sub_action is not None, "Top-level parser must have a subparsers action"

    # Choices maps subcommand names/aliases to ArgumentParser instances
    seen_parsers = set()
    for name, sub_parser in sub_action.choices.items():
        if id(sub_parser) in seen_parsers:
            continue
        seen_parsers.add(id(sub_parser))

        # Check description is set and non-empty
        assert sub_parser.description, f"Subcommand '{name}' is missing description"
        assert len(sub_parser.description.strip()) > 5, f"Subcommand '{name}' description too short: {sub_parser.description}"

        # Check help string in sub_action._choices_actions
        matching_choice = [
            choice for choice in sub_action._choices_actions if choice.dest == name
        ]
        if matching_choice:
            choice_action = matching_choice[0]
            assert choice_action.help, f"Subcommand '{name}' is missing help string"
            assert len(choice_action.help.strip()) > 5, f"Subcommand '{name}' help too short: {choice_action.help}"


def test_nested_subparsers_have_help_and_description():
    """Verify nested subparsers (theme, presentation, providers, entity) have help and descriptions."""
    parser = cli.build_parser()
    sub_action = _get_subparsers_action(parser)
    assert sub_action is not None

    nested_command_names = ["theme", "presentation", "providers", "entity"]
    for cmd_name in nested_command_names:
        sub_parser = sub_action.choices.get(cmd_name)
        assert sub_parser is not None, f"Expected subcommand '{cmd_name}'"

        nested_action = _get_subparsers_action(sub_parser)
        assert nested_action is not None, f"Subcommand '{cmd_name}' must have nested subparsers action"

        seen_nested = set()
        for nested_name, nested_p in nested_action.choices.items():
            if id(nested_p) in seen_nested:
                continue
            seen_nested.add(id(nested_p))

            assert nested_p.description, f"{cmd_name} sub-action '{nested_name}' is missing description"
            assert len(nested_p.description.strip()) > 5, f"{cmd_name} sub-action '{nested_name}' description too short"


def test_cli_help_stdout_formatting(capsys):
    """Verify parser.format_help() formats cleanly without throwing errors."""
    parser = cli.build_parser()
    help_text = parser.format_help()
    assert "usage: claire" in help_text
    assert "Quick Reference:" in help_text
    assert "presentation" in help_text
    assert "providers" in help_text
    assert "hoyowiki" in help_text
    assert "stt-scan" in help_text
    assert "stt-backfill" in help_text
    assert "artifact-migrate" in help_text
    assert "entity" in help_text


def test_e2e_cli_help_execution():
    """End-to-end test executing `claire --help` as a subprocess CLI command."""
    proc = subprocess.run(
        [sys.executable, "-m", "claire.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    stdout = proc.stdout
    assert "claire ingest" in stdout
    assert "claire search" in stdout
    assert "claire doctor --heal" in stdout
    assert "claire presentation generate" in stdout
    assert "Detailed Documentation: docs/origin/implementation/COMMANDS.md" in stdout
