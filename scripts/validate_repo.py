#!/usr/bin/env python3
import ast
import py_compile
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORE_PATH_PARTS = {".venv", "venv", "__pycache__", ".git"}


def should_include(path):
    return not any(part in IGNORE_PATH_PARTS for part in path.parts)


PYTHON_FILES = sorted(path for path in REPO_ROOT.glob("**/*.py") if should_include(path))
HTML_FILES = sorted(REPO_ROOT.glob("*.html")) + sorted(REPO_ROOT.glob("scripts/*.html"))
AGENT_FILES = sorted(REPO_ROOT.glob(".github/agents/*.agent.md"))


def display_path(path):
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


class SimpleHTMLValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.seen_title = False
        self.seen_charset = False
        self.seen_viewport = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "title":
            self.seen_title = True
        elif tag == "meta":
            if "charset" in attrs_dict:
                self.seen_charset = True
            if attrs_dict.get("name") == "viewport":
                self.seen_viewport = True


def parse_frontmatter(text):
    if not text.startswith("---\n"):
        return {}
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    frontmatter = {}
    for line in parts[0].splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"')
    return frontmatter


def validate_python_file(path, errors, warnings):
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        errors.append(f"PYTHON compile failed: {display_path(path)} -> {exc.msg}")
        return

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        errors.append(f"PYTHON syntax failed: {display_path(path)} -> {exc}")
        return

    seen_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                key = ("import", alias.name)
                if key in seen_imports:
                    warnings.append(
                        f"PYTHON duplicate import: {display_path(path)} -> {alias.name}"
                    )
                seen_imports.add(key)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                key = ("from", module, alias.name)
                if key in seen_imports:
                    warnings.append(
                        f"PYTHON duplicate from-import: {display_path(path)} -> {module}.{alias.name}"
                    )
                seen_imports.add(key)


def validate_html_file(path, errors):
    text = path.read_text(encoding="utf-8")
    if not re.match(r"(?is)\s*<!doctype html>", text):
        errors.append(f"HTML missing doctype: {display_path(path)}")

    parser = SimpleHTMLValidator()
    try:
        parser.feed(text)
    except Exception as exc:  # pragma: no cover - defensive parse guard
        errors.append(f"HTML parse failed: {display_path(path)} -> {exc}")
        return

    if not parser.seen_title:
        errors.append(f"HTML missing <title>: {display_path(path)}")
    if not parser.seen_charset:
        errors.append(f"HTML missing charset meta: {display_path(path)}")
    if not parser.seen_viewport:
        errors.append(f"HTML missing viewport meta: {display_path(path)}")


def validate_agent_file(path, errors):
    text = path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    for required_key in ("name", "description", "argument-hint"):
        if not frontmatter.get(required_key):
            errors.append(
                f"AGENT missing frontmatter '{required_key}': {display_path(path)}"
            )


def main():
    errors = []
    warnings = []

    for path in PYTHON_FILES:
        validate_python_file(path, errors, warnings)

    for path in HTML_FILES:
        validate_html_file(path, errors)

    for path in AGENT_FILES:
        validate_agent_file(path, errors)

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    if errors:
        print("Errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        f"Validation passed: {len(PYTHON_FILES)} Python files, "
        f"{len(HTML_FILES)} HTML files, {len(AGENT_FILES)} agent files checked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())