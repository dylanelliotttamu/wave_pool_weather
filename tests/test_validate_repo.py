import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "validate_repo.py"
SPEC = importlib.util.spec_from_file_location("validate_repo", MODULE_PATH)
validate_repo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_repo)


class TestValidateRepoHelpers(unittest.TestCase):
    def test_parse_frontmatter_extracts_required_keys(self):
        text = (
            "---\n"
            "name: sample-agent\n"
            "description: \"hello\"\n"
            "argument-hint: do thing\n"
            "---\n"
            "Body"
        )
        frontmatter = validate_repo.parse_frontmatter(text)
        self.assertEqual(frontmatter.get("name"), "sample-agent")
        self.assertEqual(frontmatter.get("description"), "hello")
        self.assertEqual(frontmatter.get("argument-hint"), "do thing")

    def test_parse_frontmatter_returns_empty_dict_without_header(self):
        self.assertEqual(validate_repo.parse_frontmatter("no frontmatter"), {})

    def test_should_include_excludes_venv_paths(self):
        self.assertFalse(validate_repo.should_include(Path("/tmp/repo/.venv/lib/foo.py")))
        self.assertFalse(validate_repo.should_include(Path("/tmp/repo/venv/lib/foo.py")))
        self.assertFalse(validate_repo.should_include(Path("/tmp/repo/__pycache__/foo.py")))

    def test_should_include_allows_repo_files(self):
        self.assertTrue(validate_repo.should_include(Path("/tmp/repo/scripts/pull_api_data.py")))


class TestHTMLValidation(unittest.TestCase):
    def test_valid_html_has_no_errors(self):
        html = (
            "<!DOCTYPE html><html><head>"
            "<meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
            "<title>Valid</title>"
            "</head><body></body></html>"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "ok.html"
            path.write_text(html, encoding="utf-8")
            errors = []
            validate_repo.validate_html_file(path, errors)
            self.assertEqual(errors, [])

    def test_invalid_html_missing_title_raises_error(self):
        html = (
            "<!DOCTYPE html><html><head>"
            "<meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
            "</head><body></body></html>"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bad.html"
            path.write_text(html, encoding="utf-8")
            errors = []
            validate_repo.validate_html_file(path, errors)
            self.assertTrue(any("missing <title>" in err for err in errors))


if __name__ == "__main__":
    unittest.main()