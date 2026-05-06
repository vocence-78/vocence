"""Tests for temp.jinja template."""

import os

import pytest
import jinja2


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE_NAME = "temp.jinja"
EXPECTED_CONTENT = "coderabbit-test"


@pytest.fixture
def jinja_env():
    """Return a Jinja2 Environment that loads from the repo root."""
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        keep_trailing_newline=True,
    )


@pytest.fixture
def template(jinja_env):
    """Load the temp.jinja template."""
    return jinja_env.get_template(TEMPLATE_NAME)


class TestTempJinjaExists:
    """Tests that verify the template file is accessible."""

    def test_template_file_exists(self):
        path = os.path.join(TEMPLATE_DIR, TEMPLATE_NAME)
        assert os.path.isfile(path)

    def test_template_loads_without_error(self, jinja_env):
        template = jinja_env.get_template(TEMPLATE_NAME)
        assert template is not None

    def test_template_name_matches(self, template):
        assert template.name == TEMPLATE_NAME


class TestTempJinjaRendering:
    """Tests for rendering temp.jinja."""

    def test_render_contains_expected_text(self, template):
        output = template.render()
        assert EXPECTED_CONTENT in output

    def test_render_returns_string(self, template):
        output = template.render()
        assert isinstance(output, str)

    def test_render_with_empty_context(self, template):
        output = template.render({})
        assert EXPECTED_CONTENT in output

    def test_render_with_extra_context_variables_ignored(self, template):
        output = template.render(foo="bar", baz=42)
        assert EXPECTED_CONTENT in output

    def test_render_output_starts_with_expected_text(self, template):
        output = template.render()
        assert output.startswith(EXPECTED_CONTENT)

    def test_render_is_idempotent(self, template):
        first = template.render()
        second = template.render()
        assert first == second

    def test_render_does_not_produce_empty_output(self, template):
        output = template.render()
        assert output.strip() != ""


class TestTempJinjaContent:
    """Tests for the raw content of temp.jinja."""

    def test_raw_file_content_matches_expected(self):
        path = os.path.join(TEMPLATE_DIR, TEMPLATE_NAME)
        with open(path) as f:
            content = f.read()
        assert EXPECTED_CONTENT in content

    def test_rendered_output_matches_raw_content_stripped(self, template):
        path = os.path.join(TEMPLATE_DIR, TEMPLATE_NAME)
        with open(path) as f:
            raw = f.read()
        rendered = template.render()
        assert rendered.strip() == raw.strip()

    def test_no_unresolved_jinja_blocks_in_output(self, template):
        output = template.render()
        assert "{{" not in output
        assert "}}" not in output
        assert "{%" not in output
        assert "%}" not in output

    def test_render_boundary_multiple_contexts(self, template):
        """Regression: extra context must never corrupt static-only template output."""
        contexts = [
            {},
            {"name": "test"},
            {"coderabbit": "test", "extra": True},
            {"a": 1, "b": 2, "c": 3},
        ]
        for ctx in contexts:
            output = template.render(**ctx)
            assert EXPECTED_CONTENT in output, f"Failed with context: {ctx}"
