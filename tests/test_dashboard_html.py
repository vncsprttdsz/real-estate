import re
from pathlib import Path


def test_css_braces_balanced():
    html = (Path(__file__).parent.parent / "docs" / "index.html").read_text()
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    depth = 0
    for ch in css:
        depth += ch == "{"
        depth -= ch == "}"
        assert depth >= 0, "chave '}' sobrando no CSS"
    assert depth == 0
